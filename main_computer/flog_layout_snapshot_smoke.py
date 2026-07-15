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
import hashlib
import itertools
import json
import math
import re
import tempfile
from collections import Counter, defaultdict
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
DEFAULT_RESPONSIVE_MODE = "recursive"
DEFAULT_RESPONSIVE_VIEWPORTS = (
    "wide=1600x1000,desktop=1440x900,medium=1200x820,"
    "constrained=1024x768,narrow=840x720,compact=680x720,small=560x720"
)
DEFAULT_RESPONSIVE_HYSTERESIS_PX = 48
RESPONSIVE_POLICY_VERSION = "capacity-derived-presentation-contract-v8"
RESPONSIVE_STATE_MACHINE_VERSION = "ordered-hysteretic-state-machine-v1"
RESPONSIVE_PRESENTATION_CONTRACT_VERSION = "mcel-responsive-presentations-v2"
RESPONSIVE_COVERAGE_VERSION = "sampled-capacity-coverage-v2"
RESPONSIVE_TRANSITION_PROOF_VERSION = "robust-transition-envelope-proof-v3"
RESPONSIVE_TRANSITION_EVIDENCE_PREFIX = "transition-proof-"
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
PHASE_SHARE_FLOOR_TOLERANCE = 0.0005

LAYOUT_HINT_CONTRACT_VERSION = "mcel-layout-hints-v1"
LAYOUT_HINT_MODE = "shadow-only"
LAYOUT_HINT_STRENGTHS = {"required", "strong", "preferred", "opportunistic"}
LAYOUT_HINT_PLACEMENTS = {
    "top",
    "left",
    "center",
    "right",
    "bottom",
    "tab",
    "stage",
    "trigger",
    "overlay",
}
LAYOUT_HINT_ROOT_MODELS = {"dock-workbench", "stack", "grid"}
LAYOUT_HINT_REFINEMENT_VERSION = "mcel-layout-hint-refinement-v1"
LAYOUT_HINT_RESPONSIVE_VERSION = "mcel-layout-hints-responsive-v1"
LAYOUT_HINT_MIN_ROBUST_HEADROOM = 0.01
LAYOUT_HINT_REFINEMENT_IMPROVEMENT_FLOOR = 0.002
LAYOUT_HINT_FALLBACK_POLICY_BY_PLACEMENT = {
    "right": "bounded-side-drawer",
    "bottom": "bounded-bottom-drawer",
    "tab": "tabbed-phase-support",
    "stage": "sequential-phase-stage",
    "trigger": "one-active-plus-triggers",
}
LAYOUT_HINT_FALLBACK_PROFILES = {
    "right": ["wide", "desktop"],
    "bottom": ["medium", "constrained"],
    "tab": ["narrow"],
    "stage": ["compact", "small"],
    "trigger": ["compact", "small"],
}

USER_LAYOUT_HINT_CONTRACT_VERSION = "mcel-user-layout-hints-v1"
USER_LAYOUT_HINT_OPERATION_VERSION = "mcel-user-layout-operations-v1"
USER_LAYOUT_HINT_MODE = "shadow-only"
USER_LAYOUT_HINT_BROWSER_PROOF_VERSION = "mcel-user-layout-browser-proof-v5"
USER_LAYOUT_HINT_BROWSER_MODE = "shadow-browser-proof"
USER_LAYOUT_HINT_USER_TAB_PRESENTATION = "user-tab-workbench"
USER_LAYOUT_HINT_BROWSER_VIEWPORTS = (
    ("wide", 1600),
    ("medium", 1200),
    ("narrow", 840),
    ("compact", 680),
)
USER_LAYOUT_HINT_OPERATION_KINDS = {
    "dock",
    "tab-with",
    "resize-share",
    "collapse",
    "undo",
    "reset",
}
USER_LAYOUT_HINT_MUTABILITIES = {
    "placement",
    "share",
    "collapsed",
    "tab-group",
}
USER_LAYOUT_HINT_FORBIDDEN_COORDINATE_KEYS = {
    "x",
    "y",
    "left",
    "top",
    "right",
    "bottom",
    "width",
    "height",
    "pixel",
    "pixels",
    "px",
}
USER_LAYOUT_HINT_PLACEMENT_LEVELS = {
    "top": 0,
    "left": 0,
    "center": 0,
    "right": 0,
    "bottom": 1,
    "tab": 2,
    "overlay": 2,
    "stage": 3,
    "trigger": 3,
}

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
    responsive_probe: bool = False


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


def merge_viewport_profiles(
    base_profiles: list[ViewportProfile],
    responsive_profiles: list[ViewportProfile],
) -> list[ViewportProfile]:
    """Merge profiles while preserving base names and marking generated resize probes."""

    merged: list[ViewportProfile] = []
    seen_dimensions: set[tuple[int, int]] = set()
    seen_names: set[str] = set()
    for profile in base_profiles:
        key = (profile.width, profile.height)
        if key in seen_dimensions or profile.name in seen_names:
            continue
        merged.append(profile)
        seen_dimensions.add(key)
        seen_names.add(profile.name)
    for profile in responsive_profiles:
        key = (profile.width, profile.height)
        if key in seen_dimensions or profile.name in seen_names:
            continue
        merged.append(
            ViewportProfile(
                name=profile.name,
                width=profile.width,
                height=profile.height,
                responsive_probe=True,
            )
        )
        seen_dimensions.add(key)
        seen_names.add(profile.name)
    return sorted(merged, key=lambda item: (-item.width, -item.height, item.name))


def _interpolated_probe_height(
    width: int,
    profiles: list[ViewportProfile],
) -> int:
    """Estimate a representative height without inventing a third search axis."""

    ordered = sorted(profiles, key=lambda item: item.width)
    if not ordered:
        return 720
    if width <= ordered[0].width:
        return ordered[0].height
    if width >= ordered[-1].width:
        return ordered[-1].height
    for lower, upper in zip(ordered, ordered[1:]):
        if lower.width <= width <= upper.width:
            span = max(1, upper.width - lower.width)
            ratio = (width - lower.width) / span
            return int(round(lower.height + ratio * (upper.height - lower.height)))
    return ordered[-1].height


def responsive_boundary_viewports_for_hierarchy(
    hierarchy: dict[str, Any],
    *,
    reference_profiles: list[ViewportProfile],
) -> list[ViewportProfile]:
    """Sample both sides of every capacity boundary.

    Milestone 2 keeps the search finite: only the authored adjacent fallback
    realizations are rendered at these probes.  The profile name carries those
    placements so ``candidate_applies_to_viewport`` can avoid rerunning the broad
    candidate catalog at every one-pixel boundary sample.
    """

    if not hierarchy.get("responsiveContract"):
        return []
    contract = responsive_contract_for_hierarchy(hierarchy)
    bands = sorted(
        contract.get("bands") or [],
        key=lambda item: -int(item.get("minWidth", 0) or 0),
    )
    placement_by_band = {
        "wide": ["right"],
        "medium": ["bottom"],
        "narrow": ["tab"],
        "compact": ["stage", "trigger"],
    }
    probes: list[ViewportProfile] = []
    for upper, lower in zip(bands, bands[1:]):
        boundary = int(upper.get("minWidth", 0) or 0)
        if boundary <= 0:
            continue
        placements = [
            *placement_by_band.get(str(upper.get("id") or ""), []),
            *placement_by_band.get(str(lower.get("id") or ""), []),
        ]
        placement_token = "-".join(dict.fromkeys(placements)) or "responsive"
        for side, width in (("below", boundary - 1), ("above", boundary + 1)):
            if width <= 0:
                continue
            probes.append(
                ViewportProfile(
                    name=(
                        f"boundary-{placement_token}-{side}-{width}"
                    ),
                    width=width,
                    height=_interpolated_probe_height(width, reference_profiles),
                    responsive_probe=True,
                )
            )
    return probes


def _transition_bisection_widths(
    *,
    lower: int,
    upper: int,
    max_depth: int,
) -> list[int]:
    """Return dyadic midpoint probes inside one known pass/fail bracket.

    The proof run is planned before Chromium starts, so it cannot literally wait
    for each midpoint result.  A bounded bisection tree is the deterministic
    equivalent: it samples the midpoint first, then the two remaining brackets,
    and continues only to the declared depth.  This concentrates evidence inside
    the unresolved interval instead of spending probes below or above it.
    """

    lower = int(lower)
    upper = int(upper)
    if upper < lower:
        lower, upper = upper, lower
    depth = max(1, min(6, int(max_depth)))
    intervals = [(lower, upper)]
    widths: set[int] = set()
    for _ in range(depth):
        next_intervals: list[tuple[int, int]] = []
        for left, right in intervals:
            if right - left <= 1:
                continue
            midpoint = (left + right) // 2
            if midpoint <= left or midpoint >= right:
                continue
            widths.add(midpoint)
            next_intervals.append((left, midpoint))
            next_intervals.append((midpoint, right))
        intervals = next_intervals
        if not intervals:
            break
    return sorted(widths)


def responsive_transition_proof_viewports_for_hierarchy(
    hierarchy: dict[str, Any],
    *,
    reference_profiles: list[ViewportProfile],
) -> list[ViewportProfile]:
    """Add bounded multi-resolution probes for authored transition envelopes.

    Each plan supplies a finite search envelope around one adjacent authored pair.
    Dyadic sampling concentrates evidence near the center while still reaching
    both sides of the envelope.  Only the named pair renders, so the proof does
    not re-enable broad candidate search or touch the live application.
    """

    contract = responsive_contract_for_hierarchy(hierarchy)
    plans = list(contract.get("transitionProofPlans") or [])
    if not plans:
        return []

    by_name = {profile.name: profile for profile in reference_profiles}
    probes: list[ViewportProfile] = []
    for raw in plans:
        from_placement = slugify(str(raw.get("fromPlacement") or ""))
        to_placement = slugify(str(raw.get("toPlacement") or ""))
        upper_profile = by_name.get(slugify(str(raw.get("upperProfile") or "")))
        lower_profile = by_name.get(slugify(str(raw.get("lowerProfile") or "")))

        explicit_upper = raw.get("upperWidth")
        explicit_lower = raw.get("lowerWidth")
        if explicit_upper is not None and explicit_lower is not None:
            upper = max(int(explicit_upper), int(explicit_lower))
            lower = min(int(explicit_upper), int(explicit_lower))
        elif upper_profile is not None and lower_profile is not None:
            upper = max(int(upper_profile.width), int(lower_profile.width))
            lower = min(int(upper_profile.width), int(lower_profile.width))
        else:
            continue

        if not from_placement or not to_placement or upper - lower < 2:
            continue

        widths = _transition_bisection_widths(
            lower=lower,
            upper=upper,
            max_depth=int(raw.get("maxDepth", 3) or 3),
        )
        placement_token = f"{from_placement}-{to_placement}"
        for width in widths:
            probes.append(
                ViewportProfile(
                    name=f"transition-proof-{placement_token}-{width}",
                    width=width,
                    height=_interpolated_probe_height(width, reference_profiles),
                    responsive_probe=True,
                )
            )
    return probes


def responsive_viewports_for_hierarchy(
    hierarchy: dict[str, Any],
    *,
    base_profiles: list[ViewportProfile],
    responsive_profiles: list[ViewportProfile],
    responsive_mode: str,
) -> list[ViewportProfile]:
    """Return the browser probes required for one hierarchy."""

    mode = str(responsive_mode or "off").lower()
    if mode == "off":
        return list(base_profiles)
    if mode == "recursive" and not hierarchy.get("layoutUnitTree"):
        return list(base_profiles)
    merged = merge_viewport_profiles(base_profiles, responsive_profiles)
    boundary = responsive_boundary_viewports_for_hierarchy(
        hierarchy,
        reference_profiles=merged,
    )
    transition_proof = responsive_transition_proof_viewports_for_hierarchy(
        hierarchy,
        reference_profiles=merged,
    )
    return merge_viewport_profiles(
        merge_viewport_profiles(merged, boundary),
        transition_proof,
    )


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
    """Return exclusive painted share when Stage B geometry is available."""

    if not record or not root_rect or root_rect.get("area", 0) <= 0:
        return 0.0
    for key in ("effectiveVisibleShare", "effectiveOwnedShare"):
        value = record.get(key)
        if value is not None:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                pass
    rect = _record_rect(record)
    if not rect:
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
        candidate_mode(candidate)
        in {
            "recursive-composition",
            "layout-hint-shadow",
            "layout-hint-responsive-shadow",
            "layout-hint-user-responsive-shadow",
        }
        or identity.startswith("compose--")
        or render_family in PHASE_AWARE_CANDIDATES
    )
    if support_policy == "bounded-bottom-drawer":
        placement = "bottom-drawer"
    elif support_policy == "inline-phase-stage":
        placement = "inline-stage"
    elif support_policy in {"tabbed-phase-support", "user-tab-workbench"}:
        placement = "tab-group"
    elif support_policy == "sequential-phase-stage":
        placement = "sequential-stage"
    elif support_policy == "one-active-plus-triggers":
        placement = "neutral-phase-stage"
    elif support_policy == "bounded-side-drawer":
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
    """Declare intended painted-space ownership for exclusive geometry enforcement."""

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



def _layout_hint_tokens(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item) for item in value]
    else:
        raw_values = re.split(r"[\s,]+", str(value or "").strip())
    return list(dict.fromkeys(item.strip() for item in raw_values if item.strip()))


def _layout_hint_float(
    value: Any,
    *,
    default: float = 0.0,
) -> float:
    if value in (None, ""):
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def normalize_layout_hint_contract(hierarchy: dict[str, Any]) -> dict[str, Any]:
    """Normalize HTML-shaped layout hints into one deterministic shadow contract.

    Milestone 1 deliberately reads a Python fixture that uses future-facing
    ``data-mc-layout-*`` names.  It does not inspect or modify the live app HTML.
    """

    source = copy.deepcopy(hierarchy.get("layoutHintSource") or {})
    if not source:
        return {
            "hierarchyId": hierarchy.get("id", ""),
            "version": LAYOUT_HINT_CONTRACT_VERSION,
            "mode": LAYOUT_HINT_MODE,
            "state": "notDeclared",
            "issues": [],
            "warnings": [],
            "root": {},
            "units": [],
            "sourceKind": "none",
        }

    issues: list[str] = []
    warnings: list[str] = []
    declared_version = str(source.get("version") or LAYOUT_HINT_CONTRACT_VERSION)
    if declared_version != LAYOUT_HINT_CONTRACT_VERSION:
        issues.append(
            f"unsupported layout-hint version {declared_version!r}; "
            f"expected {LAYOUT_HINT_CONTRACT_VERSION!r}"
        )

    specs = layout_unit_specs(hierarchy)
    specs_by_id = {str(unit["id"]): unit for unit in specs}
    roots = [unit for unit in specs if unit.get("parentId") is None]
    root_spec = roots[0] if len(roots) == 1 else None
    if root_spec is None:
        issues.append("layout-hint compilation requires exactly one recursive root unit")

    root_attrs = dict(source.get("attributes") or {})
    root_id = str(
        source.get("rootUnitId")
        or root_attrs.get("data-mc-layout-root")
        or (root_spec or {}).get("id")
        or ""
    )
    if root_id not in specs_by_id:
        issues.append(f"layout-hint root {root_id!r} is not a declared layout unit")

    root_model = str(root_attrs.get("data-mc-layout") or "").strip()
    if root_model not in LAYOUT_HINT_ROOT_MODELS:
        issues.append(
            f"layout-hint root model {root_model!r} is not one of "
            f"{sorted(LAYOUT_HINT_ROOT_MODELS)!r}"
        )

    root_zones = _layout_hint_tokens(root_attrs.get("data-mc-layout-zones"))
    unknown_root_zones = sorted(set(root_zones) - LAYOUT_HINT_PLACEMENTS)
    if unknown_root_zones:
        issues.append(
            "layout-hint root declares unknown zone(s): "
            + ", ".join(unknown_root_zones)
        )
    if "center" not in root_zones:
        issues.append("layout-hint dock workbench must expose a center zone")

    policy_catalog = layout_unit_policy_catalog(hierarchy)
    declared_units = dict(source.get("units") or {})
    normalized_units: list[dict[str, Any]] = []
    leaf_ids = {
        str(unit["id"])
        for unit in specs
        if bool(unit.get("leaf"))
    }
    unknown_units = sorted(set(declared_units) - set(specs_by_id))
    if unknown_units:
        issues.append(
            "layout hints reference unknown unit(s): " + ", ".join(unknown_units)
        )

    for unit_id, raw_value in declared_units.items():
        if unit_id not in specs_by_id:
            continue
        attrs = dict(raw_value or {})
        prefer = str(attrs.get("data-mc-layout-prefer") or "").strip()
        allowed = _layout_hint_tokens(attrs.get("data-mc-layout-allowed"))
        fallback = _layout_hint_tokens(attrs.get("data-mc-layout-fallback"))
        strength = str(
            attrs.get("data-mc-layout-strength") or "preferred"
        ).strip()
        policy = str(attrs.get("data-mc-layout-policy") or "").strip()
        inactive = str(
            attrs.get("data-mc-layout-inactive") or "preserve"
        ).strip()
        internal = str(attrs.get("data-mc-layout-internal") or "").strip()
        user_id = str(
            attrs.get("data-mc-layout-user-id")
            or f"{hierarchy.get('rootConcern', hierarchy.get('id', 'layout'))}.{unit_id}"
        ).strip()
        user_mutable = _layout_hint_tokens(
            attrs.get("data-mc-layout-user-mutable")
        )
        unknown_user_mutabilities = sorted(
            set(user_mutable) - USER_LAYOUT_HINT_MUTABILITIES
        )
        if unknown_user_mutabilities:
            issues.append(
                f"{unit_id} declares unknown user-layout mutability token(s): "
                + ", ".join(unknown_user_mutabilities)
            )
        min_inline = _layout_hint_float(
            attrs.get("data-mc-layout-min-inline"),
            default=0.0,
        )
        min_block = _layout_hint_float(
            attrs.get("data-mc-layout-min-block"),
            default=0.0,
        )
        max_share = _layout_hint_float(
            attrs.get("data-mc-layout-max-share"),
            default=1.0,
        )

        unknown_placements = sorted(
            (set(allowed) | set(fallback) | ({prefer} if prefer else set()))
            - LAYOUT_HINT_PLACEMENTS
        )
        if unknown_placements:
            issues.append(
                f"{unit_id} declares unknown placement(s): "
                + ", ".join(unknown_placements)
            )
        if not allowed:
            issues.append(f"{unit_id} must declare data-mc-layout-allowed")
        if not prefer:
            issues.append(f"{unit_id} must declare data-mc-layout-prefer")
        elif prefer not in allowed:
            issues.append(
                f"{unit_id} prefers {prefer!r}, but it is not in its allowed placements"
            )
        invalid_fallbacks = [item for item in fallback if item not in allowed]
        if invalid_fallbacks:
            issues.append(
                f"{unit_id} fallback placement(s) are not allowed: "
                + ", ".join(invalid_fallbacks)
            )
        if strength not in LAYOUT_HINT_STRENGTHS:
            issues.append(
                f"{unit_id} declares invalid layout-hint strength {strength!r}"
            )
        allowed_policies = {
            str(item.get("policy") or "")
            for item in policy_catalog.get(unit_id, [])
        }
        if not policy:
            issues.append(f"{unit_id} must declare data-mc-layout-policy")
        elif policy not in allowed_policies:
            issues.append(
                f"{unit_id} policy {policy!r} is not available in its local policy catalog"
            )
        if min_inline < 0 or min_block < 0:
            issues.append(f"{unit_id} minimum dimensions cannot be negative")
        if not 0 < max_share <= 1:
            issues.append(
                f"{unit_id} data-mc-layout-max-share must be in the interval (0, 1]"
            )

        normalized_units.append(
            {
                "id": unit_id,
                "role": str(specs_by_id[unit_id].get("role") or "support"),
                "slots": list(specs_by_id[unit_id].get("slots") or []),
                "prefer": prefer,
                "allowed": allowed,
                "fallback": fallback,
                "strength": strength,
                "policy": policy,
                "inactive": inactive,
                "internal": internal,
                "userId": user_id,
                "userMutable": user_mutable,
                "minInline": min_inline,
                "minBlock": min_block,
                "maxShare": max_share,
                "attributes": attrs,
            }
        )

    user_ids = [str(unit.get("userId") or "") for unit in normalized_units]
    duplicate_user_ids = sorted(
        user_id for user_id in set(user_ids) if user_id and user_ids.count(user_id) > 1
    )
    if duplicate_user_ids:
        issues.append(
            "layout hints declare duplicate stable user id(s): "
            + ", ".join(duplicate_user_ids)
        )

    missing_leaf_hints = sorted(
        leaf_ids - {str(unit["id"]) for unit in normalized_units}
    )
    if missing_leaf_hints:
        issues.append(
            "layout-hint source omits leaf unit(s): "
            + ", ".join(missing_leaf_hints)
        )

    if root_spec and root_id != str(root_spec.get("id") or ""):
        warnings.append(
            f"layout-hint root {root_id!r} differs from recursive root "
            f"{root_spec.get('id')!r}"
        )

    return {
        "hierarchyId": hierarchy.get("id", ""),
        "version": declared_version,
        "mode": LAYOUT_HINT_MODE,
        "state": "complete" if not issues else "invalid",
        "sourceKind": str(
            source.get("sourceKind") or "synthetic-data-mc-layout-attributes"
        ),
        "root": {
            "id": root_id,
            "model": root_model,
            "zones": root_zones,
            "policy": str(
                root_attrs.get("data-mc-layout-policy")
                or (root_spec or {}).get("defaultPolicy")
                or "source-order"
            ),
            "capacity": str(
                root_attrs.get("data-mc-layout-capacity") or "wide"
            ),
            "attributes": root_attrs,
        },
        "units": normalized_units,
        "issues": issues,
        "warnings": warnings,
    }


def compile_layout_hint_default(
    hierarchy: dict[str, Any],
    *,
    capacity: str = "wide",
) -> dict[str, Any]:
    """Compile authored hints into one deterministic, shadow-only dock tree."""

    contract = normalize_layout_hint_contract(hierarchy)
    if contract["state"] != "complete":
        return {
            "hierarchyId": hierarchy.get("id", ""),
            "version": LAYOUT_HINT_CONTRACT_VERSION,
            "mode": LAYOUT_HINT_MODE,
            "state": "invalid",
            "capacity": capacity,
            "issues": list(contract.get("issues") or []),
            "warnings": list(contract.get("warnings") or []),
            "dockTree": {},
            "candidate": None,
            "annotationRecommendations": [],
        }

    issues: list[str] = []
    root_zones = list(contract["root"]["zones"])
    unit_placements: dict[str, str] = {}
    zones: dict[str, list[str]] = {zone: [] for zone in root_zones}
    unit_policies: dict[str, str] = {}
    annotation_recommendations: list[dict[str, Any]] = []

    for unit in contract["units"]:
        candidates = [unit["prefer"], *unit["fallback"]]
        placement = next((item for item in candidates if item in root_zones), "")
        if not placement:
            issues.append(
                f"{unit['id']} has no preferred or fallback placement accepted by "
                f"root zones {root_zones!r}"
            )
            continue
        unit_placements[unit["id"]] = placement
        zones.setdefault(placement, []).append(unit["id"])
        unit_policies[unit["id"]] = unit["policy"]
        annotation_recommendations.append(
            {
                "targetUnitId": unit["id"],
                "attributes": copy.deepcopy(unit["attributes"]),
                "reason": (
                    "Shadow recommendation only; no live HTML or runtime file is "
                    "modified by Milestone 1."
                ),
            }
        )

    if issues:
        return {
            "hierarchyId": hierarchy.get("id", ""),
            "version": LAYOUT_HINT_CONTRACT_VERSION,
            "mode": LAYOUT_HINT_MODE,
            "state": "invalid",
            "capacity": capacity,
            "issues": issues,
            "warnings": list(contract.get("warnings") or []),
            "dockTree": {},
            "candidate": None,
            "annotationRecommendations": annotation_recommendations,
        }

    root_policy = str(contract["root"]["policy"])
    root_catalog = layout_unit_policy_catalog(hierarchy).get(
        str(contract["root"]["id"]), []
    )
    root_policies = {str(item.get("policy") or "") for item in root_catalog}
    if root_policy not in root_policies:
        issues.append(
            f"root policy {root_policy!r} is not available in the root policy catalog"
        )

    dock_tree = {
        "id": str(contract["root"]["id"]),
        "model": str(contract["root"]["model"]),
        "capacity": str(capacity),
        "policy": root_policy,
        "zones": [
            {
                "id": zone,
                "units": list(zones.get(zone) or []),
            }
            for zone in root_zones
            if zones.get(zone)
        ],
        "unitPlacements": unit_placements,
    }
    candidate_id = f"hint-compiled-default--{slugify(hierarchy.get('id', 'layout'))}"
    composition = {
        "rootPolicy": root_policy,
        "unitPolicies": unit_policies,
    }
    candidate = {
        "id": candidate_id,
        "mode": "layout-hint-shadow",
        "renderFamily": "recursive-composition",
        "compositionLabel": (
            "authored layout hints → deterministic wide default"
        ),
        "preflightScore": 100,
        "compatibilityPenalty": 0,
        "localPolicyPreflight": {
            unit_id: 100 for unit_id in unit_policies
        },
        "composition": composition,
        "shadowOnly": True,
        "minViewportWidth": 1320,
        "layoutHintCompilation": {
            "version": LAYOUT_HINT_CONTRACT_VERSION,
            "capacity": capacity,
            "dockTree": copy.deepcopy(dock_tree),
            "state": "complete" if not issues else "invalid",
        },
    }
    return {
        "hierarchyId": hierarchy.get("id", ""),
        "version": LAYOUT_HINT_CONTRACT_VERSION,
        "mode": LAYOUT_HINT_MODE,
        "state": "complete" if not issues else "invalid",
        "capacity": capacity,
        "issues": issues,
        "warnings": list(contract.get("warnings") or []),
        "contract": contract,
        "dockTree": dock_tree,
        "candidate": candidate if not issues else None,
        "annotationRecommendations": annotation_recommendations,
        "liveApplicationFilesTouched": False,
    }



def _user_layout_hint_unit_index(
    hierarchy: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    contract = normalize_layout_hint_contract(hierarchy)
    return {
        str(unit.get("userId") or ""): unit
        for unit in contract.get("units") or []
        if str(unit.get("userId") or "")
    }


def _user_layout_hint_has_raw_coordinates(operation: dict[str, Any]) -> list[str]:
    forbidden: list[str] = []
    for raw_key in operation:
        key = str(raw_key or "").strip().lower()
        normalized = key.replace("_", "-")
        tokens = [token for token in normalized.split("-") if token]
        if key in USER_LAYOUT_HINT_FORBIDDEN_COORDINATE_KEYS:
            forbidden.append(str(raw_key))
            continue
        if any(token in USER_LAYOUT_HINT_FORBIDDEN_COORDINATE_KEYS for token in tokens):
            forbidden.append(str(raw_key))
    return sorted(set(forbidden))


def normalize_user_layout_hint_profile(
    hierarchy: dict[str, Any],
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Normalize persisted user intent without interpreting it as pixel geometry.

    Milestone 3 is shadow-only.  The profile references stable semantic user ids
    declared by the layout-hint fixture and records operations such as docking,
    tabbing, share preference, collapse, undo, and reset.
    """

    source = copy.deepcopy(profile or {})
    issues: list[str] = []
    operation_issues: list[dict[str, Any]] = []
    declared_version = str(
        source.get("version") or USER_LAYOUT_HINT_CONTRACT_VERSION
    )
    operation_version = str(
        source.get("operationVersion") or USER_LAYOUT_HINT_OPERATION_VERSION
    )
    hierarchy_id = str(source.get("hierarchyId") or hierarchy.get("id") or "")
    if declared_version != USER_LAYOUT_HINT_CONTRACT_VERSION:
        issues.append(
            f"unsupported user-layout profile version {declared_version!r}; "
            f"expected {USER_LAYOUT_HINT_CONTRACT_VERSION!r}"
        )
    if operation_version != USER_LAYOUT_HINT_OPERATION_VERSION:
        issues.append(
            f"unsupported user-layout operation version {operation_version!r}; "
            f"expected {USER_LAYOUT_HINT_OPERATION_VERSION!r}"
        )
    if hierarchy_id != str(hierarchy.get("id") or ""):
        issues.append(
            f"user-layout profile targets hierarchy {hierarchy_id!r}, "
            f"not {hierarchy.get('id')!r}"
        )

    layout_contract = normalize_layout_hint_contract(hierarchy)
    if layout_contract.get("state") != "complete":
        issues.append("user-layout profile requires a complete authored layout contract")
    unit_index = _user_layout_hint_unit_index(hierarchy)
    normalized_operations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(source.get("operations") or []):
        operation = copy.deepcopy(raw or {})
        operation_id = str(operation.get("id") or f"operation-{index + 1}")
        kind = str(operation.get("kind") or "").strip().lower()
        reasons: list[str] = []
        if operation_id in seen_ids:
            reasons.append(f"duplicate operation id {operation_id!r}")
        seen_ids.add(operation_id)
        if kind not in USER_LAYOUT_HINT_OPERATION_KINDS:
            reasons.append(f"unknown user-layout operation kind {kind!r}")

        raw_coordinate_keys = _user_layout_hint_has_raw_coordinates(operation)
        if raw_coordinate_keys:
            reasons.append(
                "raw coordinate fields are not part of the semantic hint language: "
                + ", ".join(raw_coordinate_keys)
            )

        user_id = str(operation.get("userId") or "").strip()
        target_user_id = str(operation.get("targetUserId") or "").strip()
        unit = unit_index.get(user_id)
        if kind not in {"undo", "reset"}:
            if not user_id:
                reasons.append("operation must reference a stable userId")
            elif unit is None:
                reasons.append(f"unknown stable userId {user_id!r}")

        mutable = set((unit or {}).get("userMutable") or [])
        normalized: dict[str, Any] = {
            "id": operation_id,
            "kind": kind,
            "userId": user_id,
            "targetUserId": target_user_id,
            "valid": True,
            "issues": reasons,
        }
        if kind == "dock":
            placement = str(operation.get("placement") or "").strip()
            normalized["placement"] = placement
            normalized["relativeTo"] = str(operation.get("relativeTo") or "").strip()
            if "placement" not in mutable:
                reasons.append(f"{user_id!r} does not permit user placement changes")
            if placement not in (unit or {}).get("allowed", []):
                reasons.append(
                    f"{user_id!r} does not allow placement {placement!r}"
                )
        elif kind == "tab-with":
            normalized["placement"] = "tab"
            normalized["targetUserId"] = target_user_id
            if "placement" not in mutable and "tab-group" not in mutable:
                reasons.append(f"{user_id!r} does not permit tab grouping")
            if "tab" not in (unit or {}).get("allowed", []):
                reasons.append(f"{user_id!r} does not allow tab placement")
            if not target_user_id or target_user_id not in unit_index:
                reasons.append(
                    f"tab target {target_user_id!r} is not a stable layout user id"
                )
            if target_user_id and target_user_id == user_id:
                reasons.append("a unit cannot be tabbed with itself")
        elif kind == "resize-share":
            share = _layout_hint_float(operation.get("share"), default=-1.0)
            normalized["share"] = share
            if "share" not in mutable:
                reasons.append(f"{user_id!r} does not permit user share changes")
            if not 0 < share <= float((unit or {}).get("maxShare", 0) or 0):
                reasons.append(
                    f"requested share {share!r} exceeds the declared interval "
                    f"(0, {(unit or {}).get('maxShare', 0)}]"
                )
        elif kind == "collapse":
            collapsed = bool(operation.get("collapsed", True))
            normalized["collapsed"] = collapsed
            if "collapsed" not in mutable:
                reasons.append(f"{user_id!r} does not permit user collapse changes")
            if collapsed and "trigger" not in (unit or {}).get("allowed", []):
                reasons.append(
                    f"{user_id!r} cannot collapse because no trigger realization is allowed"
                )
        elif kind in {"undo", "reset"}:
            normalized["userId"] = ""
            normalized["targetUserId"] = ""

        normalized["issues"] = list(dict.fromkeys(reasons))
        normalized["valid"] = not normalized["issues"]
        if normalized["issues"]:
            operation_issues.append(
                {
                    "operationId": operation_id,
                    "kind": kind,
                    "issues": list(normalized["issues"]),
                }
            )
        normalized_operations.append(normalized)

    return {
        "version": declared_version,
        "operationVersion": operation_version,
        "mode": USER_LAYOUT_HINT_MODE,
        "profileId": str(source.get("profileId") or "anonymous-layout-profile"),
        "hierarchyId": hierarchy_id,
        "state": "complete" if not issues else "invalid",
        "issues": issues,
        "operationIssues": operation_issues,
        "operations": normalized_operations,
        "sourceKind": str(
            source.get("sourceKind") or "synthetic-semantic-user-layout-hints"
        ),
        "liveApplicationFilesTouched": False,
    }


def migrate_user_layout_hint_profile(
    hierarchy: dict[str, Any],
    profile: dict[str, Any],
    *,
    aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Map retired stable ids to current ids without inventing geometry."""

    migrated = copy.deepcopy(profile or {})
    alias_map = {
        str(key): str(value)
        for key, value in (aliases or {}).items()
        if str(key) and str(value)
    }
    changes: list[dict[str, str]] = []
    for operation in migrated.get("operations") or []:
        for field in ("userId", "targetUserId", "relativeTo"):
            old = str(operation.get(field) or "")
            if old and old in alias_map:
                operation[field] = alias_map[old]
                changes.append(
                    {
                        "operationId": str(operation.get("id") or ""),
                        "field": field,
                        "from": old,
                        "to": alias_map[old],
                    }
                )
    migrated["version"] = USER_LAYOUT_HINT_CONTRACT_VERSION
    migrated["operationVersion"] = USER_LAYOUT_HINT_OPERATION_VERSION
    normalized = normalize_user_layout_hint_profile(hierarchy, migrated)
    normalized["migration"] = {
        "state": "complete" if normalized.get("state") == "complete" else "invalid",
        "changes": changes,
        "aliasCount": len(alias_map),
    }
    return normalized


def apply_user_layout_hint_profile(
    hierarchy: dict[str, Any],
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply semantic user operations to the authored dock tree in shadow mode."""

    normalized = normalize_user_layout_hint_profile(hierarchy, profile)
    compilation = compile_layout_hint_default(hierarchy)
    if normalized.get("state") != "complete" or compilation.get("state") != "complete":
        return {
            "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
            "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
            "mode": USER_LAYOUT_HINT_MODE,
            "hierarchyId": hierarchy.get("id", ""),
            "profileId": normalized.get("profileId", ""),
            "state": "invalid",
            "issues": [
                *(normalized.get("issues") or []),
                *(compilation.get("issues") or []),
            ],
            "operationTrace": [],
            "preferences": {},
            "preferredDockTree": {},
            "liveApplicationFilesTouched": False,
        }

    contract = normalize_layout_hint_contract(hierarchy)
    units_by_user_id = {
        str(unit.get("userId") or ""): unit
        for unit in contract.get("units") or []
    }
    base_placements = dict(
        (compilation.get("dockTree") or {}).get("unitPlacements") or {}
    )
    preferences: dict[str, dict[str, Any]] = {}
    history: list[dict[str, dict[str, Any]]] = []
    trace: list[dict[str, Any]] = []

    for operation in normalized.get("operations") or []:
        before = copy.deepcopy(preferences)
        if not operation.get("valid"):
            trace.append(
                {
                    "operationId": operation.get("id", ""),
                    "kind": operation.get("kind", ""),
                    "outcome": "rejected",
                    "reasons": list(operation.get("issues") or []),
                    "preferenceRetained": True,
                }
            )
            continue

        kind = str(operation.get("kind") or "")
        user_id = str(operation.get("userId") or "")
        reasons: list[str] = []
        outcome = "accepted"
        if kind == "undo":
            if history:
                preferences = history.pop()
                reasons.append("restored the previous semantic preference state")
            else:
                outcome = "rejected"
                reasons.append("there is no accepted user-layout operation to undo")
        elif kind == "reset":
            history.append(before)
            preferences = {}
            reasons.append("restored the authored layout-hint defaults")
        else:
            history.append(before)
            current = copy.deepcopy(preferences.get(user_id) or {})
            if kind == "dock":
                current["preferredPlacement"] = str(operation.get("placement") or "")
                current["relativeTo"] = str(operation.get("relativeTo") or "")
                current.pop("tabWith", None)
            elif kind == "tab-with":
                current["preferredPlacement"] = "tab"
                current["tabWith"] = str(operation.get("targetUserId") or "")
            elif kind == "resize-share":
                current["preferredShare"] = float(operation.get("share", 0) or 0)
            elif kind == "collapse":
                current["collapsed"] = bool(operation.get("collapsed", True))
            preferences[user_id] = current
            reasons.append("stored as a semantic user preference")

        trace.append(
            {
                "operationId": operation.get("id", ""),
                "kind": kind,
                "userId": user_id,
                "outcome": outcome,
                "reasons": reasons,
                "preferenceRetained": outcome == "accepted",
                "preferenceState": copy.deepcopy(preferences),
            }
        )

    preferred_placements = dict(base_placements)
    for user_id, preference in preferences.items():
        unit = units_by_user_id.get(user_id) or {}
        unit_id = str(unit.get("id") or "")
        if not unit_id:
            continue
        if bool(preference.get("collapsed")) and "trigger" in (unit.get("allowed") or []):
            preferred_placements[unit_id] = "trigger"
        elif preference.get("preferredPlacement"):
            preferred_placements[unit_id] = str(preference["preferredPlacement"])

    dock_tree = copy.deepcopy(compilation.get("dockTree") or {})
    dock_tree["unitPlacements"] = preferred_placements
    dock_tree["userPreferences"] = copy.deepcopy(preferences)
    return {
        "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
        "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
        "mode": USER_LAYOUT_HINT_MODE,
        "hierarchyId": hierarchy.get("id", ""),
        "profileId": normalized.get("profileId", ""),
        "state": "complete",
        "issues": [],
        "operationIssues": copy.deepcopy(normalized.get("operationIssues") or []),
        "operationTrace": trace,
        "preferences": preferences,
        "preferredDockTree": dock_tree,
        "authoredDockTree": copy.deepcopy(compilation.get("dockTree") or {}),
        "historyDepth": len(history),
        "liveApplicationFilesTouched": False,
    }


def _user_layout_hint_capacity_requirement(
    hierarchy: dict[str, Any],
    width: int,
) -> tuple[dict[str, Any], int]:
    contract = responsive_contract_for_hierarchy(hierarchy)
    band = responsive_capacity_band(contract, int(width))
    return band, int(band.get("maxRemediationLevel", 0) or 0)


def resolve_user_layout_hint_at_capacity(
    hierarchy: dict[str, Any],
    applied_profile: dict[str, Any],
    *,
    width: int,
    viewport_profile: str = "",
) -> dict[str, Any]:
    """Resolve preferences against capacity while retaining unavailable intent."""

    contract = normalize_layout_hint_contract(hierarchy)
    units_by_user_id = {
        str(unit.get("userId") or ""): unit
        for unit in contract.get("units") or []
    }
    compilation = compile_layout_hint_default(hierarchy)
    authored_placements = dict(
        (compilation.get("dockTree") or {}).get("unitPlacements") or {}
    )
    preferences = copy.deepcopy(applied_profile.get("preferences") or {})
    band, required_level = _user_layout_hint_capacity_requirement(hierarchy, width)
    effective = dict(authored_placements)
    remediations: list[dict[str, Any]] = []

    for user_id, unit in units_by_user_id.items():
        unit_id = str(unit.get("id") or "")
        preference = preferences.get(user_id) or {}
        authored = str(authored_placements.get(unit_id) or unit.get("prefer") or "")
        requested = str(preference.get("preferredPlacement") or authored)
        if bool(preference.get("collapsed")) and "trigger" in (unit.get("allowed") or []):
            requested = "trigger"

        # Capacity fallback is meaningful only for units that declare progressive
        # responsive placements. Required center and persistent feedback units keep
        # their authored structural position.
        responsive_chain = [
            placement
            for placement in [requested, *list(unit.get("fallback") or [])]
            if placement in {"right", "bottom", "tab", "stage", "trigger"}
        ]
        selected = requested
        if responsive_chain and unit_id == "phase-support":
            selected = next(
                (
                    placement
                    for placement in responsive_chain
                    if int(USER_LAYOUT_HINT_PLACEMENT_LEVELS.get(placement, 0))
                    >= required_level
                ),
                "",
            )
            if not selected:
                selected = str((unit.get("fallback") or ["trigger"])[-1])
        effective[unit_id] = selected

        if selected != requested:
            remediations.append(
                {
                    "userId": user_id,
                    "unitId": unit_id,
                    "requestedPlacement": requested,
                    "effectivePlacement": selected,
                    "capacityBand": str(band.get("id") or ""),
                    "reason": (
                        f"{requested} requires more simultaneous capacity than "
                        f"{band.get('id')} guarantees; used the least restrictive "
                        "declared fallback that remains feasible"
                    ),
                    "preferenceRetained": True,
                    "restoresWhenFeasible": True,
                }
            )

    return {
        "viewportProfile": viewport_profile,
        "width": int(width),
        "capacityBand": str(band.get("id") or ""),
        "requiredRemediationLevel": required_level,
        "preferredPlacements": {
            str((units_by_user_id.get(user_id) or {}).get("id") or user_id): (
                "trigger"
                if bool(preference.get("collapsed"))
                else str(
                    preference.get("preferredPlacement")
                    or authored_placements.get(
                        str((units_by_user_id.get(user_id) or {}).get("id") or ""),
                        "",
                    )
                )
            )
            for user_id, preference in preferences.items()
        },
        "effectivePlacements": effective,
        "preferredShares": {
            user_id: float(preference.get("preferredShare", 0) or 0)
            for user_id, preference in preferences.items()
            if preference.get("preferredShare")
        },
        "remediations": remediations,
        "preferenceRetained": True,
    }


def simulate_user_layout_hint_round_trip(
    hierarchy: dict[str, Any],
    applied_profile: dict[str, Any],
    *,
    viewports: list[tuple[str, int]] | None = None,
) -> dict[str, Any]:
    probes = viewports or [
        ("wide", 1600),
        ("medium", 1200),
        ("narrow", 840),
        ("compact", 680),
        ("wide-restored", 1600),
    ]
    states = [
        resolve_user_layout_hint_at_capacity(
            hierarchy,
            applied_profile,
            width=width,
            viewport_profile=name,
        )
        for name, width in probes
    ]
    first = states[0].get("effectivePlacements") or {}
    last = states[-1].get("effectivePlacements") or {}
    return {
        "state": "complete",
        "probes": states,
        "restoredAfterRoundTrip": first == last,
        "preferenceRetainedAtEveryProbe": all(
            bool(item.get("preferenceRetained")) for item in states
        ),
    }


def build_user_layout_hint_shadow_evidence(
    hierarchies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for hierarchy in hierarchies:
        if not hierarchy.get("layoutHintSource"):
            continue
        unit_index = _user_layout_hint_unit_index(hierarchy)
        support_id = next(
            (
                user_id
                for user_id, unit in unit_index.items()
                if str(unit.get("id") or "") == "phase-support"
            ),
            "",
        )
        workflow_id = next(
            (
                user_id
                for user_id, unit in unit_index.items()
                if str(unit.get("id") or "") == "command-workflow"
            ),
            "",
        )
        identity_id = next(
            (
                user_id
                for user_id, unit in unit_index.items()
                if str(unit.get("id") or "") == "project-identity"
            ),
            "",
        )
        profile = {
            "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
            "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
            "profileId": "shadow-user-preferences",
            "hierarchyId": hierarchy.get("id", ""),
            "operations": [
                {
                    "id": "dock-support-bottom",
                    "kind": "dock",
                    "userId": support_id,
                    "placement": "bottom",
                    "relativeTo": workflow_id,
                },
                {
                    "id": "resize-support",
                    "kind": "resize-share",
                    "userId": support_id,
                    "share": 0.28,
                },
                {
                    "id": "collapse-project-identity",
                    "kind": "collapse",
                    "userId": identity_id,
                    "collapsed": True,
                },
            ],
        }
        applied = apply_user_layout_hint_profile(hierarchy, profile)
        round_trip = simulate_user_layout_hint_round_trip(hierarchy, applied)
        rejected = apply_user_layout_hint_profile(
            hierarchy,
            {
                "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
                "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
                "profileId": "rejected-invariant-example",
                "hierarchyId": hierarchy.get("id", ""),
                "operations": [
                    {
                        "id": "collapse-required-workflow",
                        "kind": "collapse",
                        "userId": workflow_id,
                        "collapsed": True,
                    }
                ],
            },
        )
        undo_reset = apply_user_layout_hint_profile(
            hierarchy,
            {
                "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
                "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
                "profileId": "undo-reset-evidence",
                "hierarchyId": hierarchy.get("id", ""),
                "operations": [
                    {
                        "id": "dock-before-undo",
                        "kind": "dock",
                        "userId": support_id,
                        "placement": "bottom",
                    },
                    {
                        "id": "resize-before-undo",
                        "kind": "resize-share",
                        "userId": support_id,
                        "share": 0.28,
                    },
                    {"id": "undo-resize", "kind": "undo"},
                    {"id": "reset-authored-default", "kind": "reset"},
                ],
            },
        )
        migrated = migrate_user_layout_hint_profile(
            hierarchy,
            {
                "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
                "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
                "profileId": "migration-evidence",
                "hierarchyId": hierarchy.get("id", ""),
                "operations": [
                    {
                        "id": "dock-retired-support-id",
                        "kind": "dock",
                        "userId": "repository.operation-support",
                        "placement": "bottom",
                        "relativeTo": "repository.workflow-shell",
                    }
                ],
            },
            aliases={
                "repository.operation-support": support_id,
                "repository.workflow-shell": workflow_id,
            },
        )
        evidence.append(
            {
                "hierarchyId": hierarchy.get("id", ""),
                "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
                "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
                "mode": USER_LAYOUT_HINT_MODE,
                "state": (
                    "complete"
                    if applied.get("state") == "complete"
                    and round_trip.get("restoredAfterRoundTrip")
                    else "invalid"
                ),
                "liveApplicationFilesTouched": False,
                "profile": copy.deepcopy(profile),
                "operationTrace": copy.deepcopy(applied.get("operationTrace") or []),
                "preferences": copy.deepcopy(applied.get("preferences") or {}),
                "roundTrip": round_trip,
                "rejectedInvariantExample": {
                    "trace": copy.deepcopy(rejected.get("operationTrace") or []),
                    "rejected": any(
                        item.get("outcome") == "rejected"
                        for item in rejected.get("operationTrace") or []
                    ),
                },
                "undoResetEvidence": {
                    "trace": copy.deepcopy(undo_reset.get("operationTrace") or []),
                    "finalPreferences": copy.deepcopy(
                        undo_reset.get("preferences") or {}
                    ),
                    "restoredAuthoredDefault": (
                        (undo_reset.get("preferredDockTree") or {}).get(
                            "unitPlacements"
                        )
                        == (undo_reset.get("authoredDockTree") or {}).get(
                            "unitPlacements"
                        )
                    ),
                },
                "migrationEvidence": {
                    "state": str(
                        (migrated.get("migration") or {}).get("state") or ""
                    ),
                    "changes": copy.deepcopy(
                        (migrated.get("migration") or {}).get("changes") or []
                    ),
                    "operations": copy.deepcopy(
                        migrated.get("operations") or []
                    ),
                },
                "storageModel": "semantic-operations-only",
                "rawPixelCoordinatesStored": False,
            }
        )
    return evidence



def user_layout_hint_browser_profiles(
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the bounded M3.2 semantic profiles rendered in Chromium.

    These profiles exercise placement, share, tab grouping, collapse, responsive
    remediation, and restoration.  They remain synthetic shadow inputs and do not
    inspect or mutate live application files.
    """

    unit_index = _user_layout_hint_unit_index(hierarchy)
    support_id = next(
        (
            user_id
            for user_id, unit in unit_index.items()
            if str(unit.get("id") or "") == "phase-support"
        ),
        "",
    )
    workflow_id = next(
        (
            user_id
            for user_id, unit in unit_index.items()
            if str(unit.get("id") or "") == "command-workflow"
        ),
        "",
    )
    identity_id = next(
        (
            user_id
            for user_id, unit in unit_index.items()
            if str(unit.get("id") or "") == "project-identity"
        ),
        "",
    )
    if not support_id or not workflow_id or not identity_id:
        return []

    definitions = [
        {
            "profileId": "bottom-28-collapsed-identity",
            "description": (
                "Support prefers the bottom at 28% while project identity is "
                "collapsed to its legal trigger realization."
            ),
            "operations": [
                {
                    "id": "dock-support-bottom",
                    "kind": "dock",
                    "userId": support_id,
                    "placement": "bottom",
                    "relativeTo": workflow_id,
                },
                {
                    "id": "resize-support-28",
                    "kind": "resize-share",
                    "userId": support_id,
                    "share": 0.28,
                },
                {
                    "id": "collapse-project-identity",
                    "kind": "collapse",
                    "userId": identity_id,
                    "collapsed": True,
                },
            ],
            "proveRestorationFingerprint": True,
        },
        {
            "profileId": "right-24",
            "description": "Support prefers the right wall at a 24% share.",
            "operations": [
                {
                    "id": "dock-support-right",
                    "kind": "dock",
                    "userId": support_id,
                    "placement": "right",
                    "relativeTo": workflow_id,
                },
                {
                    "id": "resize-support-24",
                    "kind": "resize-share",
                    "userId": support_id,
                    "share": 0.24,
                },
            ],
            "proveRestorationFingerprint": True,
        },
        {
            "profileId": "tab-with-workflow",
            "description": (
                "Support is tabbed with the command/workflow unit and falls back "
                "to a sequential stage only when compact capacity requires it."
            ),
            "operations": [
                {
                    "id": "tab-support-with-workflow",
                    "kind": "tab-with",
                    "userId": support_id,
                    "targetUserId": workflow_id,
                }
            ],
            "proveRestorationFingerprint": True,
        },
    ]

    profiles: list[dict[str, Any]] = []
    for definition in definitions:
        source = {
            "version": USER_LAYOUT_HINT_CONTRACT_VERSION,
            "operationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
            "profileId": definition["profileId"],
            "hierarchyId": hierarchy.get("id", ""),
            "operations": copy.deepcopy(definition["operations"]),
            "sourceKind": "synthetic-browser-user-layout-proof",
        }
        applied = apply_user_layout_hint_profile(hierarchy, source)
        profiles.append(
            {
                **copy.deepcopy(definition),
                "source": source,
                "applied": applied,
            }
        )
    return profiles


def _user_layout_required_context_floor(
    hierarchy: dict[str, Any],
    slot: str,
) -> float:
    """Return the same bounded companion floor used by phase scoring."""

    node = _node_by_slot(hierarchy).get(slot, {})
    declared = float(node.get("minVisibleShare", 0.012) or 0.012)
    return max(0.010, min(declared, 0.055))


def _user_tab_workbench_summary_contract(
    hierarchy: dict[str, Any],
) -> dict[str, Any]:
    """Describe root-relative summary floors for browser calibration.

    M3.6 deliberately does not guess the effective root share of the nested
    ``command-workflow`` unit.  The HTML carries only semantic root-relative
    requirements plus a conservative calibration scaffold.  Chromium then
    measures the realized root, parent, and summary widths, resolves the local
    track shares, applies them, and remeasures before any FLOG gate runs.
    """

    requested_root = {
        "workflow": _user_layout_required_context_floor(hierarchy, "workflow"),
        "command": _user_layout_required_context_floor(hierarchy, "command"),
    }
    root_margin = 0.004
    allocated_root = {
        slot: min(0.09, share + root_margin)
        for slot, share in requested_root.items()
    }
    track_safety_root = 0.018
    requested_track_root = min(
        0.24,
        sum(allocated_root.values()) + track_safety_root,
    )
    return {
        "requestedRootShares": requested_root,
        "allocatedRootShares": allocated_root,
        "resolvedLocalShares": {},
        "parentEffectiveRootShare": None,
        "requestedTrackRootShare": requested_track_root,
        "rootMarginShare": root_margin,
        "trackSafetyRootShare": track_safety_root,
        "calibrationMode": "realized-summary-slot-two-pass",
        "source": "required-companion-floor-browser-calibrated",
    }

def _user_layout_candidate_policy_for_placement(
    placement: str,
    *,
    user_authored_tab: bool = False,
) -> str:
    if str(placement or "") == "tab" and user_authored_tab:
        return USER_LAYOUT_HINT_USER_TAB_PRESENTATION
    return {
        "right": "bounded-side-drawer",
        "bottom": "bounded-bottom-drawer",
        "tab": "tabbed-phase-support",
        "stage": "sequential-phase-stage",
        "trigger": "one-active-plus-triggers",
    }.get(str(placement or ""), "")


def compile_user_layout_hint_browser_candidates(
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compile accepted user profiles into bounded browser-rendered candidates.

    M3.6 renders three semantic profiles across wide, medium, narrow, and compact
    capacity.  Every profile also receives a restored-wide duplicate for an exact
    painted-geometry fingerprint comparison, yielding 15 candidate/viewport
    aggregates and 90 phase PNGs for the six-phase Git fixture.
    """

    if not hierarchy.get("layoutHintSource"):
        return []
    compilation = compile_layout_hint_default(hierarchy)
    base_candidate = compilation.get("candidate")
    if compilation.get("state") != "complete" or not isinstance(base_candidate, dict):
        return []

    base_composition = copy.deepcopy(base_candidate.get("composition") or {})
    base_policies = dict(base_composition.get("unitPolicies") or {})
    if base_policies.get("command-workflow") == "command-over-dominant":
        base_policies["command-workflow"] = "command-inline-header"

    contract = normalize_layout_hint_contract(hierarchy)
    user_to_unit = {
        str(unit.get("userId") or ""): str(unit.get("id") or "")
        for unit in contract.get("units") or []
        if unit.get("userId")
    }
    support_user_id = next(
        (
            user_id
            for user_id, unit_id in user_to_unit.items()
            if unit_id == "phase-support"
        ),
        "",
    )

    candidates: list[dict[str, Any]] = []
    for profile in user_layout_hint_browser_profiles(hierarchy):
        applied = profile.get("applied") or {}
        if applied.get("state") != "complete":
            continue
        preferences = copy.deepcopy(applied.get("preferences") or {})
        support_preference = preferences.get(support_user_id) or {}
        collapsed_user_ids = sorted(
            user_id
            for user_id, preference in preferences.items()
            if bool(preference.get("collapsed"))
        )
        tab_with_user_id = str(support_preference.get("tabWith") or "")
        preferred_share = float(
            support_preference.get("preferredShare", 0) or 0
        )

        for viewport_name, width in USER_LAYOUT_HINT_BROWSER_VIEWPORTS:
            resolved = resolve_user_layout_hint_at_capacity(
                hierarchy,
                applied,
                width=width,
                viewport_profile=viewport_name,
            )
            effective_placements = dict(
                resolved.get("effectivePlacements") or {}
            )
            support_placement = str(
                effective_placements.get("phase-support") or "right"
            )
            user_authored_tab = bool(
                tab_with_user_id
                and support_placement == "tab"
                and viewport_name in {"wide", "medium"}
            )
            support_policy = _user_layout_candidate_policy_for_placement(
                support_placement,
                user_authored_tab=user_authored_tab,
            )
            if not support_policy:
                continue
            summary_contract = (
                _user_tab_workbench_summary_contract(hierarchy)
                if user_authored_tab
                else {}
            )

            composition = {
                "rootPolicy": str(
                    base_composition.get("rootPolicy")
                    or "dominant-workflow-stack"
                ),
                "unitPolicies": {
                    **base_policies,
                    "phase-support": support_policy,
                },
            }
            dock_tree = copy.deepcopy(compilation.get("dockTree") or {})
            unit_placements = dict(dock_tree.get("unitPlacements") or {})
            unit_placements.update(effective_placements)
            dock_tree["unitPlacements"] = unit_placements
            dock_tree["userPreferences"] = copy.deepcopy(preferences)

            mutation = {
                "version": USER_LAYOUT_HINT_BROWSER_PROOF_VERSION,
                "mode": USER_LAYOUT_HINT_BROWSER_MODE,
                "profileId": str(profile.get("profileId") or ""),
                "profileDescription": str(profile.get("description") or ""),
                "viewportProfile": viewport_name,
                "variant": "preferred-or-remediated",
                "preferredPlacement": str(
                    support_preference.get("preferredPlacement")
                    or (compilation.get("dockTree") or {})
                    .get("unitPlacements", {})
                    .get("phase-support", "right")
                ),
                "effectivePlacement": support_placement,
                "preferredShare": preferred_share,
                "collapsedUserIds": collapsed_user_ids,
                "collapsedUnitIds": sorted(
                    user_to_unit.get(user_id, "")
                    for user_id in collapsed_user_ids
                    if user_to_unit.get(user_id)
                ),
                "tabWithUserId": tab_with_user_id,
                "tabWithUnitId": user_to_unit.get(tab_with_user_id, ""),
                "userTabWorkbench": user_authored_tab,
                "summaryMinimumShares": copy.deepcopy(summary_contract),
                "restorationFingerprintRequired": bool(
                    profile.get("proveRestorationFingerprint")
                ),
                "capacityBand": str(resolved.get("capacityBand") or ""),
                "remediations": copy.deepcopy(
                    resolved.get("remediations") or []
                ),
                "preferenceRetained": bool(
                    resolved.get("preferenceRetained", False)
                ),
                "restoresWhenFeasible": all(
                    bool(item.get("restoresWhenFeasible", False))
                    for item in resolved.get("remediations") or []
                )
                if resolved.get("remediations")
                else True,
                "sourceOperations": copy.deepcopy(
                    (profile.get("source") or {}).get("operations") or []
                ),
                "liveApplicationFilesTouched": False,
            }
            candidate = {
                "id": (
                    f"user-layout--{slugify(profile.get('profileId', 'profile'))}"
                    f"--{slugify(viewport_name)}"
                ),
                "mode": "layout-hint-user-responsive-shadow",
                "renderFamily": "recursive-composition",
                "compositionLabel": (
                    f"user layout {profile.get('profileId', '')} → "
                    f"{support_placement} at {viewport_name}"
                ),
                "preflightScore": 100,
                "compatibilityPenalty": 0,
                "localPolicyPreflight": {
                    unit_id: 100
                    for unit_id in composition["unitPolicies"]
                },
                "composition": composition,
                "shadowOnly": True,
                "responsiveEligible": False,
                "responsivePlacement": support_placement,
                "presentationContractKey": (
                    USER_LAYOUT_HINT_USER_TAB_PRESENTATION
                    if user_authored_tab
                    else {
                        "right": "wide",
                        "bottom": "medium",
                        "tab": "narrow",
                        "stage": "compact",
                        "trigger": "compact",
                    }.get(support_placement, "")
                ),
                "userTabWorkbench": user_authored_tab,
                "viewportProfiles": [viewport_name],
                "layoutHintCompilation": {
                    "version": USER_LAYOUT_HINT_BROWSER_PROOF_VERSION,
                    "capacity": support_placement,
                    "dockTree": dock_tree,
                    "state": "complete",
                    "liveApplicationFilesTouched": False,
                },
                "userLayoutMutation": mutation,
            }
            candidates.append(candidate)

            if (
                viewport_name == "wide"
                and bool(profile.get("proveRestorationFingerprint"))
            ):
                restored = copy.deepcopy(candidate)
                restored["id"] = (
                    f"user-layout--{slugify(profile.get('profileId', 'profile'))}"
                    "--wide-restored"
                )
                restored["compositionLabel"] = (
                    f"user layout {profile.get('profileId', '')} → "
                    "wide restored after compact round trip"
                )
                restored["userLayoutMutation"]["variant"] = "wide-restored"
                restored["userLayoutMutation"]["restorationReferenceCandidate"] = (
                    candidate["id"]
                )
                candidates.append(restored)
    return candidates


def _user_layout_summary_delivery_diagnostics(
    item: dict[str, Any],
    mutation: dict[str, Any],
) -> dict[str, Any]:
    """Verify browser-calibrated root-relative summary delivery.

    M3.6 fails closed unless every phase that realizes both compact summaries
    reports a completed slot-fill calibration.  The final gate consumes both
    Chromium's effective painted root shares and the calibrated grid-slot fill.
    """

    if not bool(mutation.get("userTabWorkbench", False)):
        return {
            "required": False,
            "passed": True,
            "rows": [],
            "calibrations": [],
            "failures": [],
        }

    contract = dict(mutation.get("summaryMinimumShares") or {})
    requested_root = dict(contract.get("requestedRootShares") or {})
    allocated_root = dict(contract.get("allocatedRootShares") or {})
    rows: list[dict[str, Any]] = []
    calibrations: list[dict[str, Any]] = []
    failures: list[str] = []
    required_calibration_count = 0

    for phase_measurement in item.get("phaseMeasurements") or []:
        phase = str(phase_measurement.get("phase") or "")
        realization_states = dict(
            phase_measurement.get("realizationStates") or {}
        )
        records = {
            str(record.get("slot") or ""): record
            for record in (phase_measurement.get("examples") or {}).get(
                "nodes", []
            )
            if record.get("slot")
        }
        root_rect = _root_rect_from_measurement(phase_measurement)
        compact_slots = [
            slot
            for slot in requested_root
            if realization_states.get(slot) == "compact-summary"
        ]
        calibration = copy.deepcopy(
            phase_measurement.get("userLayoutCalibration") or {}
        )
        needs_calibration = bool(requested_root) and all(
            realization_states.get(slot) == "compact-summary"
            for slot in requested_root
        )
        if needs_calibration:
            required_calibration_count += 1
            calibration_row = {
                "phase": phase,
                **calibration,
            }
            calibrations.append(calibration_row)
            if (
                calibration.get("state") != "complete"
                or not bool(calibration.get("passed", False))
            ):
                failures.append(
                    f"{phase} user-tab summary calibration did not complete "
                    f"(state={calibration.get('state', 'missing')})"
                )
            if not bool(calibration.get("summarySlotAllocationPassed", False)):
                failures.append(
                    f"{phase} user-tab summary grid tracks did not receive their "
                    "allocated root-relative shares"
                )
            if not bool(calibration.get("summarySlotFillPassed", False)):
                fill = dict(calibration.get("summarySlotFillRatios") or {})
                failures.append(
                    f"{phase} semantic summary nodes did not fill their allocated "
                    f"grid tracks (command={float(fill.get('command', 0) or 0):.1%}, "
                    f"workflow={float(fill.get('workflow', 0) or 0):.1%})"
                )

        calibration_local = dict(
            calibration.get("resolvedLocalShares") or {}
        )
        calibration_slots = dict(
            calibration.get("summarySlotRootShares") or {}
        )
        calibration_fill = dict(
            calibration.get("summarySlotFillRatios") or {}
        )
        calibration_parent = float(
            calibration.get("finalParentRootShare", 0) or 0
        )
        for slot, required_share in requested_root.items():
            if realization_states.get(slot) != "compact-summary":
                continue
            record = records.get(slot)
            measured_share = _record_area_share(record, root_rect)
            gate = phase_share_floor_check(
                measured_share,
                float(required_share or 0),
            )
            allocated_share = float(
                allocated_root.get(slot, required_share) or required_share
            )
            resolved_local_share = float(
                calibration_local.get(slot, 0) or 0
            )
            slot_root_share = float(
                calibration_slots.get(slot, 0) or 0
            )
            slot_fill_ratio = float(
                calibration_fill.get(slot, 0) or 0
            )
            slot_gate = phase_share_floor_check(
                slot_root_share,
                allocated_share,
            )
            row = {
                "phase": phase,
                "slot": slot,
                "requiredRootShare": float(required_share or 0),
                "allocatedRootShare": allocated_share,
                "resolvedLocalShare": resolved_local_share,
                "measuredParentRootShare": calibration_parent,
                "summarySlotRootShare": slot_root_share,
                "summarySlotAllocatedShareMet": bool(slot_gate["met"]),
                "summarySlotFillRatio": slot_fill_ratio,
                "summarySlotFilled": slot_fill_ratio >= 0.98,
                "measuredRootShare": measured_share,
                "requiredRootShareMet": bool(gate["met"]),
                "rawHeadroom": float(gate["rawHeadroom"]),
                "shortfall": float(gate["shortfall"]),
                "calibrationState": str(
                    calibration.get("state") or "missing"
                ),
            }
            rows.append(row)
            if not gate["met"]:
                failures.append(
                    f"{phase} {slot} summary delivered "
                    f"{measured_share:.2%} of root against requested "
                    f"{float(required_share or 0):.2%} after browser-calibrating "
                    f"{resolved_local_share:.2%} of its measured parent"
                )

    if not rows:
        failures.append(
            "user-tab workbench produced no browser-measured compact summaries"
        )
    if required_calibration_count == 0:
        failures.append(
            "user-tab workbench produced no phase requiring summary calibration"
        )

    return {
        "required": True,
        "passed": not failures,
        "coordinateSystem": "browser-measured-summary-slot-fill-two-pass",
        "calibrationMode": str(contract.get("calibrationMode") or ""),
        "requestedRootShares": requested_root,
        "allocatedRootShares": allocated_root,
        "requestedTrackRootShare": float(
            contract.get("requestedTrackRootShare", 0) or 0
        ),
        "trackSafetyRootShare": float(
            contract.get("trackSafetyRootShare", 0) or 0
        ),
        "calibrations": calibrations,
        "rows": rows,
        "failures": failures,
    }

def _user_layout_browser_measurement_summary(
    item: dict[str, Any],
) -> dict[str, Any]:
    mutation = copy.deepcopy(
        (item.get("candidateSpec") or {}).get("userLayoutMutation") or {}
    )
    phase_measurements = item.get("phaseMeasurements") or []
    hard_failure_count = sum(
        int((phase.get("classification") or {}).get("hardFailureCount", 0) or 0)
        for phase in phase_measurements
    )
    blocked = max(
        [
            int(
                (phase.get("classification") or {}).get(
                    "blockedCriticalControlCount", 0
                )
                or 0
            )
            for phase in phase_measurements
        ]
        or [0]
    )
    outcome = layout_hint_measurement_outcome(item)
    summary_delivery = _user_layout_summary_delivery_diagnostics(
        item,
        mutation,
    )
    browser_gate_passed = (
        outcome.get("outcome") in {"accepted", "accepted-with-warning"}
        and hard_failure_count == 0
        and blocked == 0
        and bool(summary_delivery.get("passed", False))
    )
    return {
        "candidate": str(item.get("candidate") or ""),
        "viewportProfile": str(item.get("viewportProfile") or ""),
        "width": int(item.get("viewportWidth", 0) or 0),
        "height": int(item.get("viewportHeight", 0) or 0),
        "profileId": str(mutation.get("profileId") or ""),
        "variant": str(mutation.get("variant") or ""),
        "preferredPlacement": str(mutation.get("preferredPlacement") or ""),
        "effectivePlacement": str(mutation.get("effectivePlacement") or ""),
        "preferredShare": float(mutation.get("preferredShare", 0) or 0),
        "collapsedUnitIds": list(mutation.get("collapsedUnitIds") or []),
        "tabWithUnitId": str(mutation.get("tabWithUnitId") or ""),
        "userTabWorkbench": bool(mutation.get("userTabWorkbench", False)),
        "summaryMinimumShares": copy.deepcopy(
            mutation.get("summaryMinimumShares") or {}
        ),
        "summaryAllocationDiagnostics": summary_delivery,
        "summaryAllocationPassed": bool(
            summary_delivery.get("passed", False)
        ),
        "restorationFingerprintRequired": bool(
            mutation.get("restorationFingerprintRequired", False)
        ),
        "remediations": copy.deepcopy(mutation.get("remediations") or []),
        "preferenceRetained": bool(mutation.get("preferenceRetained", False)),
        "restoresWhenFeasible": bool(
            mutation.get("restoresWhenFeasible", False)
        ),
        "status": measurement_status(item),
        "outcome": str(outcome.get("outcome") or ""),
        "score": measurement_score(item),
        "worstPhaseHeadroom": float(
            measurement_margin_evidence(item).get("worstPhaseHeadroom", -1)
        ),
        "hardFailureCount": hard_failure_count,
        "blockedCriticalControlCount": blocked,
        "browserGatePassed": browser_gate_passed,
        "renderedPolicyFingerprint": str(
            item.get("renderedPolicyFingerprint") or ""
        ),
        "phaseSnapshots": copy.deepcopy(item.get("phaseSnapshots") or {}),
    }


def analyze_user_layout_hint_browser_evidence(
    *,
    hierarchies: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    semantic_evidence: list[dict[str, Any]] | None = None,
    responsive_policies: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate M3.2 Chromium evidence for semantic user-mutated dock trees."""

    semantic_by_hierarchy = {
        str(item.get("hierarchyId") or ""): item
        for item in (semantic_evidence or [])
    }
    responsive_by_hierarchy = {
        str(item.get("hierarchyId") or ""): item
        for item in (responsive_policies or [])
    }
    reports: list[dict[str, Any]] = []
    for hierarchy in hierarchies:
        hierarchy_id = str(hierarchy.get("id") or "")
        rows = [
            _user_layout_browser_measurement_summary(item)
            for item in measurements
            if str(item.get("hierarchyId") or "") == hierarchy_id
            and str(item.get("candidateMode") or "")
            == "layout-hint-user-responsive-shadow"
        ]
        if not rows:
            continue

        responsive_policy = responsive_by_hierarchy.get(hierarchy_id) or {}
        base_hysteresis_verified = bool(
            responsive_policy
            and responsive_policy.get("state") == "pass"
            and bool(responsive_policy.get("wideToNarrowStable", False))
            and bool(responsive_policy.get("narrowToWideStable", False))
            and int(
                responsive_policy.get("unverifiedTransitionCount", 0) or 0
            )
            == 0
            and int(
                responsive_policy.get(
                    "insufficientHysteresisTransitionCount", 0
                )
                or 0
            )
            == 0
        )

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get("profileId") or "")].append(row)

        profile_reports: list[dict[str, Any]] = []
        for profile_id, profile_rows in sorted(grouped.items()):
            profile_rows.sort(
                key=lambda row: (
                    -int(row.get("width", 0) or 0),
                    str(row.get("variant") or ""),
                    str(row.get("candidate") or ""),
                )
            )
            wide_reference = next(
                (
                    row
                    for row in profile_rows
                    if row.get("viewportProfile") == "wide"
                    and row.get("variant") != "wide-restored"
                ),
                None,
            )
            wide_restored = next(
                (
                    row
                    for row in profile_rows
                    if row.get("variant") == "wide-restored"
                ),
                None,
            )
            restoration_required = any(
                bool(row.get("restorationFingerprintRequired"))
                for row in profile_rows
            )
            if not restoration_required:
                restoration_status = "not-applicable"
                restoration_match: bool | None = None
            elif not wide_reference or not wide_restored:
                restoration_status = "missing-required-proof"
                restoration_match = False
            elif (
                bool(wide_reference.get("renderedPolicyFingerprint"))
                and wide_reference.get("renderedPolicyFingerprint")
                == wide_restored.get("renderedPolicyFingerprint")
            ):
                restoration_status = "matched"
                restoration_match = True
            else:
                restoration_status = "mismatched"
                restoration_match = False
            all_browser_valid = all(
                bool(row.get("browserGatePassed")) for row in profile_rows
            )
            retained = all(
                bool(row.get("preferenceRetained")) for row in profile_rows
            )
            ordered_capacity_rows = [
                row
                for row in sorted(
                    profile_rows,
                    key=lambda row: -int(row.get("width", 0) or 0),
                )
                if row.get("variant") != "wide-restored"
            ]
            placement_sequence: list[str] = []
            for row in ordered_capacity_rows:
                placement = str(row.get("effectivePlacement") or "")
                if placement and (
                    not placement_sequence
                    or placement_sequence[-1] != placement
                ):
                    placement_sequence.append(placement)
            remediation_levels = [
                int(USER_LAYOUT_HINT_PLACEMENT_LEVELS.get(placement, 0))
                for placement in placement_sequence
            ]
            monotonic_capacity_path = remediation_levels == sorted(
                remediation_levels
            )
            legal_transition_pairs = {
                ("right", "bottom"),
                ("bottom", "tab"),
                ("tab", "stage"),
            }
            transition_pairs = list(
                zip(placement_sequence, placement_sequence[1:])
            )
            transition_chain_covered = all(
                pair in legal_transition_pairs for pair in transition_pairs
            )
            hysteresis_coverage_passed = (
                base_hysteresis_verified
                and monotonic_capacity_path
                and transition_chain_covered
            )
            profile_reports.append(
                {
                    "profileId": profile_id,
                    "state": (
                        "complete"
                        if all_browser_valid
                        and retained
                        and hysteresis_coverage_passed
                        and (not restoration_required or restoration_match)
                        else "invalid"
                    ),
                    "browserGatePassed": all_browser_valid,
                    "placementSequence": placement_sequence,
                    "monotonicCapacityPath": monotonic_capacity_path,
                    "transitionChainCovered": transition_chain_covered,
                    "baseHysteresisVerified": base_hysteresis_verified,
                    "hysteresisCoveragePassed": hysteresis_coverage_passed,
                    "preferenceRetainedAtEveryCapacity": retained,
                    "restorationFingerprintRequired": restoration_required,
                    "restorationFingerprintStatus": restoration_status,
                    "restorationFingerprintMatch": restoration_match,
                    "restorationReferenceFingerprint": (
                        str(
                            (wide_reference or {}).get(
                                "renderedPolicyFingerprint", ""
                            )
                        )
                    ),
                    "restorationObservedFingerprint": (
                        str(
                            (wide_restored or {}).get(
                                "renderedPolicyFingerprint", ""
                            )
                        )
                    ),
                    "proofs": profile_rows,
                    "browserAggregateCount": len(profile_rows),
                    "pngProofCount": sum(
                        len(row.get("phaseSnapshots") or {})
                        for row in profile_rows
                    ),
                }
            )

        semantic = semantic_by_hierarchy.get(hierarchy_id) or {}
        reports.append(
            {
                "hierarchyId": hierarchy_id,
                "version": USER_LAYOUT_HINT_BROWSER_PROOF_VERSION,
                "mode": USER_LAYOUT_HINT_BROWSER_MODE,
                "state": (
                    "complete"
                    if profile_reports
                    and all(
                        item.get("state") == "complete"
                        for item in profile_reports
                    )
                    else "invalid"
                ),
                "profiles": profile_reports,
                "profileCount": len(profile_reports),
                "browserAggregateCount": sum(
                    int(item.get("browserAggregateCount", 0) or 0)
                    for item in profile_reports
                ),
                "pngProofCount": sum(
                    int(item.get("pngProofCount", 0) or 0)
                    for item in profile_reports
                ),
                "allBrowserGatesPassed": all(
                    bool(item.get("browserGatePassed"))
                    for item in profile_reports
                ),
                "allPreferencesRetained": all(
                    bool(item.get("preferenceRetainedAtEveryCapacity"))
                    for item in profile_reports
                ),
                "allHysteresisCoveragePassed": all(
                    bool(item.get("hysteresisCoveragePassed"))
                    for item in profile_reports
                ),
                "baseResponsivePolicyState": str(
                    responsive_policy.get("state") or ""
                ),
                "allRequiredRestorationsMatched": all(
                    (
                        not bool(item.get("restorationFingerprintRequired"))
                        or str(item.get("restorationFingerprintStatus") or "")
                        == "matched"
                    )
                    for item in profile_reports
                ),
                "undoResetEvidence": copy.deepcopy(
                    semantic.get("undoResetEvidence") or {}
                ),
                "migrationEvidence": copy.deepcopy(
                    semantic.get("migrationEvidence") or {}
                ),
                "liveApplicationFilesTouched": False,
            }
        )
    return reports


def layout_hint_shadow_candidate_spec(
    hierarchy: dict[str, Any],
) -> dict[str, Any] | None:
    compilation = compile_layout_hint_default(hierarchy)
    candidate = compilation.get("candidate")
    if not isinstance(candidate, dict):
        return None
    result = copy.deepcopy(candidate)
    # Milestone 2 reuses the deterministic wide default as the authored right-dock
    # realization instead of rendering a duplicate responsive candidate.
    result["responsiveEligible"] = True
    result["responsivePlacement"] = "right"
    result["presentationContractKey"] = "wide"
    derivation = derive_responsive_capacity_bands_from_hints(hierarchy)
    wide_band = next(
        (
            item
            for item in derivation.get("bands") or []
            if str(item.get("id") or "") == "wide"
        ),
        {},
    )
    result["minViewportWidth"] = int(wide_band.get("minWidth", 1320) or 1320)
    result["viewportProfiles"] = ["wide", "desktop"]
    return result


def candidate_shadow_only(candidate: str | dict[str, Any]) -> bool:
    return bool(isinstance(candidate, dict) and candidate.get("shadowOnly"))


def candidate_applies_to_viewport(
    candidate: str | dict[str, Any],
    viewport: ViewportProfile,
) -> bool:
    if not isinstance(candidate, dict):
        return not viewport.name.startswith(("boundary-", "transition-proof-"))
    if viewport.name.startswith(("boundary-", "transition-proof-")):
        if not candidate_responsive_eligible(candidate):
            return False
        placement = str(candidate.get("responsivePlacement") or "")
        return bool(placement and f"-{placement}-" in f"-{viewport.name}-")
    minimum = int(candidate.get("minViewportWidth", 0) or 0)
    maximum = int(candidate.get("maxViewportWidth", 0) or 0)
    if viewport.width < minimum:
        return False
    if maximum and viewport.width > maximum:
        return False
    names = _layout_hint_tokens(candidate.get("viewportProfiles"))
    return not names or viewport.name in names


def candidate_responsive_eligible(candidate: str | dict[str, Any]) -> bool:
    """Return whether a shadow candidate may participate in responsive selection."""

    return bool(isinstance(candidate, dict) and candidate.get("responsiveEligible"))


def compile_layout_hint_responsive_candidates(
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compile the authored fallback chain into deterministic FLOG-only candidates.

    Milestone 2 still does not inspect or mutate live application files.  It turns
    the normalized hint contract into one explicit composition per responsive
    placement so Chromium can verify the authored chain rather than rediscovering
    it through broad family search.
    """

    compilation = compile_layout_hint_default(hierarchy)
    candidate = compilation.get("candidate")
    if compilation.get("state") != "complete" or not isinstance(candidate, dict):
        return []

    base_composition = copy.deepcopy(candidate.get("composition") or {})
    unit_policies = dict(base_composition.get("unitPolicies") or {})
    # Milestone 1E established the safer default.  The synthetic contract is also
    # updated below, but keeping this normalization here makes the compiler robust
    # to older fixture snapshots.
    if unit_policies.get("command-workflow") == "command-over-dominant":
        unit_policies["command-workflow"] = "command-inline-header"

    result: list[dict[str, Any]] = []
    for placement in ("bottom", "tab", "stage", "trigger"):
        support_policy = LAYOUT_HINT_FALLBACK_POLICY_BY_PLACEMENT.get(placement, "")
        profiles = list(LAYOUT_HINT_FALLBACK_PROFILES.get(placement, []))
        if not support_policy or not profiles:
            continue
        composition = {
            "rootPolicy": str(
                base_composition.get("rootPolicy") or "dominant-workflow-stack"
            ),
            "unitPolicies": {
                **unit_policies,
                "phase-support": support_policy,
            },
        }
        dock_tree = copy.deepcopy(compilation.get("dockTree") or {})
        unit_placements = dict(dock_tree.get("unitPlacements") or {})
        unit_placements["phase-support"] = placement
        dock_tree["unitPlacements"] = unit_placements
        zones = []
        for zone in dock_tree.get("zones") or []:
            zone_copy = copy.deepcopy(zone)
            zone_copy["units"] = [
                unit_id
                for unit_id in zone_copy.get("units") or []
                if unit_id != "phase-support"
            ]
            zones.append(zone_copy)
        target_zone = next(
            (zone for zone in zones if zone.get("id") == placement),
            None,
        )
        if target_zone is None:
            target_zone = {"id": placement, "units": []}
            zones.append(target_zone)
        target_zone.setdefault("units", []).append("phase-support")
        dock_tree["zones"] = zones

        result.append(
            {
                "id": (
                    f"hint-responsive-{slugify(placement)}--"
                    f"{slugify(hierarchy.get('id', 'layout'))}"
                ),
                "mode": "layout-hint-responsive-shadow",
                "renderFamily": "recursive-composition",
                "compositionLabel": (
                    f"authored responsive hints → {placement} realization"
                ),
                "preflightScore": 100,
                "compatibilityPenalty": 0,
                "localPolicyPreflight": {
                    unit_id: 100
                    for unit_id in composition["unitPolicies"]
                },
                "composition": composition,
                "shadowOnly": True,
                "responsiveEligible": True,
                "responsivePlacement": placement,
                "presentationContractKey": {
                    "bottom": "medium",
                    "tab": "narrow",
                    "stage": "compact",
                    "trigger": "compact",
                }.get(placement, ""),
                "viewportProfiles": profiles,
                "layoutHintCompilation": {
                    "version": LAYOUT_HINT_RESPONSIVE_VERSION,
                    "capacity": placement,
                    "dockTree": dock_tree,
                    "state": "complete",
                    "liveApplicationFilesTouched": False,
                },
            }
        )
    return result




def layout_hint_policy_differences(
    authored_policies: dict[str, Any],
    candidate_policies: dict[str, Any],
) -> list[dict[str, str]]:
    """Return the smallest local-policy edits between two compositions."""

    unit_ids = sorted(set(authored_policies) | set(candidate_policies))
    return [
        {
            "unitId": unit_id,
            "currentPolicy": str(authored_policies.get(unit_id) or ""),
            "suggestedPolicy": str(candidate_policies.get(unit_id) or ""),
        }
        for unit_id in unit_ids
        if str(authored_policies.get(unit_id) or "")
        != str(candidate_policies.get(unit_id) or "")
    ]


def layout_hint_measurement_outcome(
    item: dict[str, Any],
    *,
    robust_headroom: float = LAYOUT_HINT_MIN_ROBUST_HEADROOM,
) -> dict[str, Any]:
    """Classify hint evidence without allowing a score to repair infeasibility."""

    status = measurement_status(item)
    evidence = measurement_margin_evidence(item)
    headroom = float(evidence["worstPhaseHeadroom"])
    hard_failure_count = int(
        (item.get("phaseFit") or {}).get("hardFailureCount", 0) or 0
    ) + int(
        (item.get("layoutUnitFit") or {}).get("hardFailureCount", 0) or 0
    )
    acceptable = (
        status in ACCEPTABLE_LAYOUT_STATUSES
        and hard_failure_count == 0
        and headroom >= -PHASE_SHARE_FLOOR_TOLERANCE
    )
    robust = acceptable and (
        headroom + PHASE_SHARE_FLOOR_TOLERANCE >= float(robust_headroom)
    )
    if robust:
        outcome = "accepted"
    elif acceptable:
        outcome = "accepted-with-warning"
    else:
        outcome = "rejected"

    classification = item.get("classification") or {}
    return {
        "outcome": outcome,
        "status": status,
        "score": measurement_score(item),
        "selectionScoreRaw": float(evidence["selectionScoreRaw"]),
        "worstPhaseHeadroom": headroom,
        "requiredRobustHeadroom": float(robust_headroom),
        "hardFailureCount": hard_failure_count,
        "firstFailure": str(
            next(iter(classification.get("failureReasons") or []), "")
        ),
    }


def layout_hint_fallback_plan(
    hierarchy: dict[str, Any],
    *,
    unit_id: str = "phase-support",
) -> dict[str, Any]:
    """Prepare authored fallback placements for browser-evidence lookup."""

    contract = normalize_layout_hint_contract(hierarchy)
    unit = next(
        (
            item
            for item in contract.get("units") or []
            if str(item.get("id") or "") == unit_id
        ),
        None,
    )
    if contract.get("state") != "complete" or unit is None:
        return {
            "unitId": unit_id,
            "state": "notDeclared",
            "preferredPlacement": "",
            "preferredPolicy": "",
            "chain": [],
        }

    available_policies = {
        str(item.get("policy") or "")
        for item in layout_unit_policy_catalog(hierarchy).get(unit_id, [])
    }
    placements = [str(unit.get("prefer") or ""), *list(unit.get("fallback") or [])]
    chain: list[dict[str, Any]] = []
    for index, placement in enumerate(item for item in placements if item):
        policy = LAYOUT_HINT_FALLBACK_POLICY_BY_PLACEMENT.get(placement, "")
        if not policy:
            state = "unmapped"
            reason = (
                f"placement {placement!r} has no distinct local realization policy "
                "in the current FLOG catalog"
            )
        elif policy not in available_policies:
            state = "unavailable"
            reason = (
                f"placement {placement!r} maps to policy {policy!r}, but that policy "
                f"is not available for {unit_id}"
            )
        else:
            state = "ready"
            reason = "placement has a distinct local policy available for browser evidence"
        chain.append(
            {
                "index": index,
                "placement": placement,
                "policy": policy,
                "state": state,
                "viewportProfiles": list(
                    LAYOUT_HINT_FALLBACK_PROFILES.get(placement, [])
                ),
                "reason": reason,
            }
        )
    return {
        "unitId": unit_id,
        "state": "complete",
        "preferredPlacement": str(unit.get("prefer") or ""),
        "preferredPolicy": str(unit.get("policy") or ""),
        "chain": chain,
    }


def _layout_hint_authored_policies(
    hierarchy: dict[str, Any],
) -> dict[str, str]:
    compilation = compile_layout_hint_default(hierarchy)
    candidate = compilation.get("candidate") or {}
    composition = candidate.get("composition") or {}
    return {
        str(unit_id): str(policy)
        for unit_id, policy in (composition.get("unitPolicies") or {}).items()
    }


def _layout_hint_measurements_for(
    measurements: list[dict[str, Any]],
    *,
    hierarchy_id: str,
    viewport_profile: str | None = None,
) -> list[dict[str, Any]]:
    return [
        item
        for item in measurements
        if str(item.get("hierarchyId") or "") == hierarchy_id
        and (
            viewport_profile is None
            or str(item.get("viewportProfile") or "") == viewport_profile
        )
    ]


def _layout_hint_candidate_policies(item: dict[str, Any]) -> dict[str, str]:
    return {
        str(unit_id): str(policy)
        for unit_id, policy in (
            (item.get("unitComposition") or {}).get("unitPolicies") or {}
        ).items()
        if str(unit_id) != str(
            (item.get("unitComposition") or {}).get("rootUnitId") or ""
        )
    }


def _layout_hint_best_matching_candidate(
    measurements: list[dict[str, Any]],
    *,
    authored_policies: dict[str, str],
    required_policy: tuple[str, str] | None = None,
    exact_difference_count: int | None = None,
) -> dict[str, Any] | None:
    candidates: list[tuple[int, tuple[Any, ...], dict[str, Any]]] = []
    for item in measurements:
        if bool(item.get("shadowOnly")) and not bool(item.get("responsiveEligible")):
            continue
        if str(item.get("candidateMode") or "") not in {
            "recursive-composition",
            "layout-hint-responsive-shadow",
        }:
            continue
        if bool(item.get("renderedEquivalenceExcludedFromRanking")):
            continue
        policies = _layout_hint_candidate_policies(item)
        if required_policy:
            unit_id, policy = required_policy
            if policies.get(unit_id) != policy:
                continue
        differences = layout_hint_policy_differences(authored_policies, policies)
        if exact_difference_count is not None and len(differences) != exact_difference_count:
            continue
        candidates.append(
            (
                len(differences),
                measurement_ranking_sort_key(item),
                item,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    return candidates[0][2]


def analyze_layout_hint_refinements(
    *,
    hierarchies: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn browser measurements into minimal shadow hint corrections.

    This is Milestone 1E: it compares the deterministic authored default with
    already-rendered recursive alternatives and prepares fallback evidence.  It
    never edits HTML, CSS, or application runtime files.
    """

    reports: list[dict[str, Any]] = []
    for hierarchy in hierarchies:
        if not hierarchy.get("layoutHintSource"):
            continue
        hierarchy_id = str(hierarchy.get("id") or "")
        compilation = compile_layout_hint_default(hierarchy)
        authored_policies = _layout_hint_authored_policies(hierarchy)
        hierarchy_measurements = _layout_hint_measurements_for(
            measurements,
            hierarchy_id=hierarchy_id,
        )
        shadow_measurements = [
            item
            for item in hierarchy_measurements
            if bool(item.get("shadowOnly"))
        ]
        for item in shadow_measurements:
            item["layoutHintOutcome"] = layout_hint_measurement_outcome(item)

        authored_by_profile = {
            str(item.get("viewportProfile") or ""): item
            for item in shadow_measurements
            if str(item.get("candidate") or "")
            == str((compilation.get("candidate") or {}).get("id") or "")
        }
        reference_profile = (
            "desktop"
            if "desktop" in authored_by_profile
            else ("wide" if "wide" in authored_by_profile else "")
        )
        authored_reference = authored_by_profile.get(reference_profile)
        authored_evidence = (
            layout_hint_measurement_outcome(authored_reference)
            if authored_reference
            else {}
        )

        single_hint_comparisons: list[dict[str, Any]] = []
        recommended_revisions: list[dict[str, Any]] = []
        if authored_reference:
            peers = _layout_hint_measurements_for(
                hierarchy_measurements,
                hierarchy_id=hierarchy_id,
                viewport_profile=reference_profile,
            )
            alternatives = []
            for item in peers:
                if bool(item.get("shadowOnly")):
                    continue
                policies = _layout_hint_candidate_policies(item)
                differences = layout_hint_policy_differences(
                    authored_policies, policies
                )
                if len(differences) != 1:
                    continue
                alternatives.append(item)
            alternatives.sort(key=measurement_ranking_sort_key)
            for alternative in alternatives:
                policies = _layout_hint_candidate_policies(alternative)
                differences = layout_hint_policy_differences(
                    authored_policies, policies
                )
                alternative_evidence = layout_hint_measurement_outcome(alternative)
                improvement = (
                    float(alternative_evidence["worstPhaseHeadroom"])
                    - float(authored_evidence.get("worstPhaseHeadroom", -1.0))
                )
                row = {
                    "candidate": str(alternative.get("candidate") or ""),
                    "viewportProfile": reference_profile,
                    "changeCount": 1,
                    "changes": differences,
                    "authoredOutcome": copy.deepcopy(authored_evidence),
                    "alternativeOutcome": alternative_evidence,
                    "headroomImprovement": improvement,
                    "browserVerified": True,
                }
                single_hint_comparisons.append(row)

            viable = [
                row
                for row in single_hint_comparisons
                if row["alternativeOutcome"]["outcome"] != "rejected"
                and (
                    row["alternativeOutcome"]["outcome"] == "accepted"
                    or row["headroomImprovement"]
                    >= LAYOUT_HINT_REFINEMENT_IMPROVEMENT_FLOOR
                )
            ]
            if viable:
                viable.sort(
                    key=lambda row: (
                        0
                        if row["alternativeOutcome"]["outcome"] == "accepted"
                        else 1,
                        -float(row["headroomImprovement"]),
                        -float(
                            row["alternativeOutcome"]["selectionScoreRaw"]
                        ),
                        row["candidate"],
                    )
                )
                best = viable[0]
                change = best["changes"][0]
                recommended_revisions.append(
                    {
                        **change,
                        "candidate": best["candidate"],
                        "viewportProfile": reference_profile,
                        "reason": (
                            "A one-unit policy change improves the browser-verified "
                            "worst-phase safety margin."
                        ),
                        "currentHeadroom": authored_evidence.get(
                            "worstPhaseHeadroom", -1
                        ),
                        "suggestedHeadroom": best["alternativeOutcome"][
                            "worstPhaseHeadroom"
                        ],
                        "headroomImprovement": best["headroomImprovement"],
                        "browserVerified": True,
                        "applyAutomatically": False,
                    }
                )

        fallback_plan = layout_hint_fallback_plan(hierarchy)
        fallback_evidence: list[dict[str, Any]] = []
        for entry in fallback_plan.get("chain") or []:
            row = copy.deepcopy(entry)
            row["measurements"] = []
            if entry.get("state") == "ready":
                for profile in entry.get("viewportProfiles") or []:
                    profile_measurements = _layout_hint_measurements_for(
                        hierarchy_measurements,
                        hierarchy_id=hierarchy_id,
                        viewport_profile=str(profile),
                    )
                    match = _layout_hint_best_matching_candidate(
                        profile_measurements,
                        authored_policies=authored_policies,
                        required_policy=(
                            str(fallback_plan["unitId"]),
                            str(entry["policy"]),
                        ),
                    )
                    if match is None:
                        row["measurements"].append(
                            {
                                "viewportProfile": profile,
                                "state": "notMeasured",
                                "outcome": "unknown",
                            }
                        )
                    else:
                        outcome = layout_hint_measurement_outcome(match)
                        row["measurements"].append(
                            {
                                "viewportProfile": profile,
                                "candidate": str(match.get("candidate") or ""),
                                "policyDifferences": layout_hint_policy_differences(
                                    authored_policies,
                                    _layout_hint_candidate_policies(match),
                                ),
                                **outcome,
                            }
                        )
            fallback_evidence.append(row)

        reports.append(
            {
                "hierarchyId": hierarchy_id,
                "version": LAYOUT_HINT_REFINEMENT_VERSION,
                "mode": LAYOUT_HINT_MODE,
                "state": (
                    "complete"
                    if compilation.get("state") == "complete"
                    and authored_reference is not None
                    else "incomplete"
                ),
                "robustHeadroom": LAYOUT_HINT_MIN_ROBUST_HEADROOM,
                "referenceViewportProfile": reference_profile,
                "authoredCandidate": str(
                    (compilation.get("candidate") or {}).get("id") or ""
                ),
                "authoredPolicies": authored_policies,
                "authoredEvidenceByViewport": {
                    profile: layout_hint_measurement_outcome(item)
                    for profile, item in sorted(authored_by_profile.items())
                },
                "singleHintComparisons": single_hint_comparisons,
                "recommendedContractRevisions": recommended_revisions,
                "fallbackPreparation": {
                    **fallback_plan,
                    "chain": fallback_evidence,
                },
                "liveApplicationFilesTouched": False,
                "applicationMutationAllowed": False,
            }
        )
    return reports


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
            generated = recursive_composition_candidate_specs(
                hierarchy,
                max_candidates=len(LAYOUT_CANDIDATES),
            )
            hinted = layout_hint_shadow_candidate_spec(hierarchy)
            if hinted:
                generated.append(hinted)
            generated.extend(compile_layout_hint_responsive_candidates(hierarchy))
            generated.extend(compile_user_layout_hint_browser_candidates(hierarchy))
            return generated
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
    mode = candidate_mode(candidate)
    generated = isinstance(candidate, dict) and mode == "recursive-composition"
    hinted = isinstance(candidate, dict) and mode in {
        "layout-hint-shadow",
        "layout-hint-responsive-shadow",
        "layout-hint-user-responsive-shadow",
    }
    return {
        "candidate": identity,
        "enabled": True,
        "rootUnitId": root["id"],
        "rootPolicy": root_policy,
        "unitPolicies": unit_policies,
        "parallelBranches": [unit["id"] for unit in specs if unit.get("leaf")],
        "searchMode": (
            (
                (
                    "deterministic-user-layout-hint-compilation"
                    if mode == "layout-hint-user-responsive-shadow"
                    else "deterministic-responsive-layout-hint-compilation"
                )
                if mode in {
                    "layout-hint-responsive-shadow",
                    "layout-hint-user-responsive-shadow",
                }
                else "deterministic-layout-hint-compilation"
            )
            if hinted
            else (
                "generated-bounded-recursive-composition"
                if generated
                else "bounded-recursive-composition"
            )
        ),
        "origin": (
            (
                (
                    "user-responsive-layout-hints-shadow"
                    if mode == "layout-hint-user-responsive-shadow"
                    else "authored-responsive-layout-hints-shadow"
                )
                if mode in {
                    "layout-hint-responsive-shadow",
                    "layout-hint-user-responsive-shadow",
                }
                else "authored-layout-hints-shadow"
            )
            if hinted
            else ("generated-policy-tuple" if generated else "legacy-family-mapping")
        ),
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
            if realization_states.get(slot)
            in {"full-active", "persistent", "compact-summary", "inactive-panel"}
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


def responsive_presentation_band_for_candidate(
    hierarchy: dict[str, Any],
    candidate: str | dict[str, Any] | None,
    viewport: ViewportProfile,
) -> tuple[str, str]:
    """Bind responsive semantics to the rendered realization.

    Milestone 2.1 treats geometry and semantic presentation as one atomic state.
    Width still decides which authored candidates are sampled, but it may not swap
    a desktop contract underneath an unchanged side-drawer realization.
    """

    if candidate is None:
        contract = responsive_contract_for_hierarchy(hierarchy)
        band = responsive_capacity_band(contract, viewport.width)
        return str(band.get("id") or "wide"), "viewport-fallback"

    placement = ""
    if isinstance(candidate, dict):
        explicit_key = str(candidate.get("presentationContractKey") or "")
        if explicit_key:
            return explicit_key, "realization-policy"
        placement = str(candidate.get("responsivePlacement") or "")
    if not placement:
        policy = candidate_phase_policy(candidate)
        placement = {
            "side-drawer": "right",
            "bottom-drawer": "bottom",
            "inline-stage": "tab",
            "tab-group": "tab",
            "sequential-stage": "stage",
            "neutral-phase-stage": "trigger",
        }.get(str(policy.get("activeSupportPlacement") or ""), "")

    band_by_placement = {
        "right": "wide",
        "bottom": "medium",
        "tab": "narrow",
        "stage": "compact",
        "trigger": "compact",
    }
    if placement in band_by_placement:
        return band_by_placement[placement], "realization-policy"

    contract = responsive_contract_for_hierarchy(hierarchy)
    band = responsive_capacity_band(contract, viewport.width)
    return str(band.get("id") or "wide"), "viewport-fallback"


def responsive_phase_scenario(
    hierarchy: dict[str, Any],
    scenario: dict[str, Any],
    viewport: ViewportProfile,
    candidate: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one phase using the contract owned by the rendered realization.

    Capacity selects which authored realization is eligible.  The realization then
    selects its semantic presentation contract.  Geometry and semantics therefore
    change atomically instead of changing the floor under an unchanged layout.
    """

    base = copy.deepcopy(scenario)
    if not hierarchy.get("responsiveContract"):
        return base

    contract = responsive_contract_for_hierarchy(hierarchy)
    viewport_band = responsive_capacity_band(contract, viewport.width)
    presentation_band_id, contract_source = responsive_presentation_band_for_candidate(
        hierarchy,
        candidate,
        viewport,
    )
    phase = str(base.get("phase") or "default")
    presentations = contract.get("phasePresentations") or {}
    band_presentations = presentations.get(presentation_band_id) or {}
    override = copy.deepcopy(
        band_presentations.get(phase)
        or band_presentations.get("*")
        or {}
    )
    if not override:
        base["responsivePresentation"] = {
            "version": RESPONSIVE_PRESENTATION_CONTRACT_VERSION,
            "capacityBand": str(viewport_band.get("id") or ""),
            "presentationBand": presentation_band_id,
            "contractSource": contract_source,
            "mode": "base-phase-contract",
            "viewportWidth": viewport.width,
            "viewportHeight": viewport.height,
            "transformed": False,
        }
        return base

    list_fields = {
        "requiredSlots",
        "activeSupportSlots",
        "collapsedSlots",
        "summarySlots",
        "reachableSlots",
    }
    for key, value in override.items():
        if key in list_fields:
            base[key] = list(dict.fromkeys(str(item) for item in (value or [])))
        elif key not in {"reasonSuffix"}:
            base[key] = copy.deepcopy(value)

    original_reason = str(scenario.get("reason") or "")
    suffix = str(override.get("reasonSuffix") or "").strip()
    if suffix:
        base["reason"] = f"{original_reason} {suffix}".strip()
    base["responsivePresentation"] = {
        "version": RESPONSIVE_PRESENTATION_CONTRACT_VERSION,
        "capacityBand": str(viewport_band.get("id") or ""),
        "presentationBand": presentation_band_id,
        "contractSource": contract_source,
        "mode": str(override.get("presentationMode") or "capacity-relative"),
        "viewportWidth": viewport.width,
        "viewportHeight": viewport.height,
        "transformed": True,
        "baseDominantSlot": str(scenario.get("dominantSlot") or hierarchy["focusSlot"]),
        "dominantSlot": str(base.get("dominantSlot") or hierarchy["focusSlot"]),
        "summarySlots": list(base.get("summarySlots") or []),
        "reachableSlots": list(base.get("reachableSlots") or []),
        "returnToSlot": str(base.get("returnToSlot") or ""),
    }
    return base

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
    summary_slots = list(dict.fromkeys(str(slot) for slot in (scenario.get("summarySlots") or [])))
    reachable_slots = list(dict.fromkeys(str(slot) for slot in (scenario.get("reachableSlots") or [])))
    referenced = {
        dominant_slot,
        *required_slots,
        *active_support_slots,
        *collapsed_slots,
        *summary_slots,
        *reachable_slots,
    }
    unknown = sorted(referenced - known_slots)
    if unknown:
        raise ValueError(
            f"Phase {scenario.get('phase', '<unknown>')} for {hierarchy.get('id', '<unknown>')} "
            f"references unknown slot(s): {', '.join(unknown)}"
        )

    active_set = {
        dominant_slot,
        *required_slots,
        *active_support_slots,
        *summary_slots,
    }
    collapsed_set = (set(collapsed_slots) | set(reachable_slots)) - active_set
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
            elif slot in summary_slots:
                state = "compact-summary"
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
        if state == "compact-summary":
            node["summaryReturnTo"] = str(
                scenario.get("returnToSlot") or hierarchy.get("focusSlot") or ""
            )
            node["summaryReason"] = str(
                scenario.get("summaryReason")
                or "Compact context retained for the active responsive stage."
            )
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
    required_companions = [
        slot
        for slot in [*required_slots, *summary_slots]
        if slot != dominant_slot
    ]
    visible_full_slots = {
        slot
        for slot, state in realization_states.items()
        if state in {"full-active", "persistent", "compact-summary", "inactive-panel"}
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
    base_contract["forbiddenDefaultHidden"] = list(
        dict.fromkeys([dominant_slot, *required_slots, *summary_slots])
    )
    base_contract["summarySlots"] = list(summary_slots)
    base_contract["reachableSlots"] = list(reachable_slots)
    base_contract["returnToSlot"] = str(scenario.get("returnToSlot") or "")

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
    realized["phaseSummarySlots"] = sorted(summary_slots)
    realized["phaseReachableSlots"] = sorted(reachable_slots)
    realized["responsivePresentation"] = copy.deepcopy(
        scenario.get("responsivePresentation") or {}
    )
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


def phase_share_floor_check(
    actual: float,
    floor: float,
    *,
    tolerance: float = PHASE_SHARE_FLOOR_TOLERANCE,
) -> dict[str, Any]:
    """Return the authoritative absolute phase-floor decision.

    A tiny tolerance absorbs browser subpixel noise.  Headroom is clamped to
    zero when the share is within that tolerance so a passing candidate never
    reports negative worst-phase headroom.
    """

    actual_value = max(0.0, float(actual or 0.0))
    floor_value = max(0.0, float(floor or 0.0))
    tolerance_value = max(0.0, float(tolerance or 0.0))
    raw_headroom = actual_value - floor_value
    met = raw_headroom >= -tolerance_value
    return {
        "met": bool(met),
        "actual": actual_value,
        "floor": floor_value,
        "tolerance": tolerance_value,
        "rawHeadroom": raw_headroom,
        "headroom": max(0.0, raw_headroom) if met else raw_headroom,
        "shortfall": max(0.0, floor_value - actual_value),
    }


def phase_share_floor_failure_reason(
    phase: str,
    slot: str,
    gate: dict[str, Any],
) -> str:
    return (
        f"{phase} phase gives {slot} {float(gate.get('actual', 0.0)):.2%} "
        f"against phase floor {float(gate.get('floor', 0.0)):.2%} "
        f"(shortfall {float(gate.get('shortfall', 0.0)):.2%}; "
        f"tolerance {float(gate.get('tolerance', 0.0)):.2%})"
    )


def apply_phase_floor_gate_to_measurement(
    realized_hierarchy: dict[str, Any],
    measurement: dict[str, Any],
) -> dict[str, Any]:
    """Canonicalize one Chromium state's absolute floor gate in Python.

    Chromium applies the same contract while rendering overlays.  This pass
    makes the Python helper authoritative before contract, phase, and ranking
    aggregation, preventing browser and aggregate status from drifting.
    """

    facts = measurement.setdefault("geometryFacts", {})
    actual = float(
        facts.get(
            "effectiveFocusShare",
            facts.get("focusShare", 0.0),
        )
        or 0.0
    )
    floor = float(
        realized_hierarchy.get(
            "minFocusShare",
            facts.get("minFocusShare", 0.0),
        )
        or 0.0
    )
    phase = str(
        measurement.get("phase")
        or realized_hierarchy.get("phase")
        or "default"
    )
    slot = str(
        realized_hierarchy.get("focusSlot")
        or measurement.get("focusSlot")
        or "focus"
    )
    gate = phase_share_floor_check(actual, floor)
    reason = phase_share_floor_failure_reason(phase, slot, gate)

    facts["phaseFloorTolerance"] = gate["tolerance"]
    facts["focusFloorMet"] = gate["met"]
    facts["focusFloorShortfall"] = gate["shortfall"]
    facts["focusRawHeadroom"] = gate["rawHeadroom"]
    facts["focusHeadroom"] = gate["headroom"]

    classification = measurement.setdefault("classification", {})
    classification["phaseFloorGate"] = copy.deepcopy(gate)
    warnings = classification.setdefault("warnings", [])
    failure_reasons = classification.setdefault("failureReasons", [])
    if not gate["met"]:
        if reason not in warnings:
            warnings.append(reason)
        if reason not in failure_reasons:
            failure_reasons.append(reason)
        classification["status"] = "fail"
    return measurement


def _phase_score_value(parts: list[tuple[float, float]]) -> float:
    """Return the unrounded weighted score for margin-aware ranking."""

    total = 0.0
    weight = 0.0
    for score, score_weight in parts:
        total += max(0.0, min(100.0, float(score))) * score_weight
        weight += score_weight
    return total / weight if weight else 0.0


def _phase_score_from_parts(parts: list[tuple[float, float]]) -> int:
    return round(_phase_score_value(parts))


def _score_share_floor_value(
    actual: float,
    floor: float,
    *,
    full_at: float | None = None,
) -> float:
    if floor <= 0:
        return 100.0
    if actual >= (full_at or floor):
        return 100.0
    return max(0.0, min(1.0, actual / floor)) * 100.0


def _score_share_floor(actual: float, floor: float, *, full_at: float | None = None) -> int:
    return round(_score_share_floor_value(actual, floor, full_at=full_at))


def _score_inactive_tax_value(tax: float, budget: float) -> float:
    if tax <= budget:
        return 100.0
    # A static layout with a phase selector permanently consuming space should
    # fall quickly once it exceeds the budget, but not become impossible if the
    # excess is small.
    excess = tax - budget
    return max(0.0, 100.0 - (excess / max(0.01, budget * 2.4)) * 100.0)


def _score_inactive_tax(tax: float, budget: float) -> int:
    return round(_score_inactive_tax_value(tax, budget))

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
    weighted_raw_total = 0.0
    weight_total = 0.0

    for scenario in scenarios:
        phase = str(scenario["phase"])
        measurement = measurements_by_phase.get(phase)
        effective_scenario = (
            copy.deepcopy(measurement.get("phaseScenario") or {})
            if measurement
            else {}
        ) or copy.deepcopy(scenario)
        phase_weight = float(effective_scenario.get("weight", 1.0) or 1.0)
        dominant_slot = str(
            effective_scenario.get("dominantSlot") or hierarchy["focusSlot"]
        )
        required_slots = list(effective_scenario.get("requiredSlots") or [])
        summary_slots = list(effective_scenario.get("summarySlots") or [])
        active_support_slots = list(
            effective_scenario.get("activeSupportSlots") or []
        )
        collapsed_slots = list(effective_scenario.get("collapsedSlots") or [])
        min_dominant = float(
            effective_scenario.get(
                "minDominantShare", hierarchy.get("minFocusShare", 0.32)
            )
            or 0.0
        )
        target_dominant = float(
            effective_scenario.get(
                "targetDominantShare",
                hierarchy.get("desiredFocusShare", min_dominant),
            )
            or min_dominant
        )
        max_inactive_tax = float(
            effective_scenario.get("maxInactiveTax", 0.12) or 0.12
        )

        if not measurement:
            reason = f"{phase} phase has no independent browser measurement"
            hard_failures.append(reason)
            evaluated.append(
                {
                    "phase": phase,
                    "score": 0,
                    "rawScore": 0.0,
                    "weight": phase_weight,
                    "dominantSlot": dominant_slot,
                    "dominantShare": 0.0,
                    "minDominantShare": min_dominant,
                    "targetDominantShare": target_dominant,
                    "dominantFloorMet": False,
                    "dominantFloorTolerance": PHASE_SHARE_FLOOR_TOLERANCE,
                    "dominantFloorShortfall": min_dominant,
                    "dominantRawHeadroom": -min_dominant,
                    "dominantHeadroom": -min_dominant,
                    "dominantTargetDelta": -target_dominant,
                    "requiredSlots": required_slots,
                    "summarySlots": summary_slots,
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
        dominant_score_raw = _score_share_floor_value(
            dominant_share, min_dominant, full_at=target_dominant
        )
        dominant_score = round(dominant_score_raw)
        if dominant_state == "compact-trigger":
            dominant_score_raw = 0.0
            dominant_score = 0
        dominant_floor_gate = phase_share_floor_check(dominant_share, min_dominant)
        dominant_raw_headroom = float(dominant_floor_gate["rawHeadroom"])
        dominant_headroom = float(dominant_floor_gate["headroom"])
        dominant_target_delta = dominant_share - target_dominant

        missing_required: list[str] = []
        weak_required: list[str] = []
        required_parts: list[tuple[float, float]] = []
        required_parts_raw: list[tuple[float, float]] = []
        for slot in [*required_slots, *summary_slots]:
            record = records.get(slot)
            node = nodes_by_slot.get(slot, {})
            share = _visible_share_for_record(record, root_rect)
            floor = float(node.get("minVisibleShare", 0.012) or 0.012)
            floor = max(0.010, min(floor, 0.055))
            realization = _record_realization(record, node, root_rect)
            if not record or realization == "compact-trigger":
                missing_required.append(slot)
                required_parts.append((0, 1.0))
                required_parts_raw.append((0.0, 1.0))
            else:
                slot_score_raw = _score_share_floor_value(
                    share, floor, full_at=max(floor, floor * 1.45)
                )
                slot_score = round(slot_score_raw)
                required_parts.append((slot_score, 1.0))
                required_parts_raw.append((slot_score_raw, 1.0))
                if slot_score < 72:
                    weak_required.append(slot)

        active_support_parts: list[tuple[float, float]] = []
        active_support_parts_raw: list[tuple[float, float]] = []
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
            support_score_raw = _score_share_floor_value(
                share, floor, full_at=full_at
            )
            support_score = round(support_score_raw)
            if realization not in {"full-active", "persistent", "inactive-panel"}:
                support_score_raw = 0.0
                support_score = 0
            active_support_parts.append((support_score, 0.85))
            active_support_parts_raw.append((support_score_raw, 0.85))
            if support_score < 72:
                weak_active_support.append(slot)

        active_set = (
            set(active_support_slots)
            | {dominant_slot}
            | set(required_slots)
            | set(summary_slots)
        )
        inactive_slots = [slot for slot in collapsed_slots if slot not in active_set]
        inactive_tax = sum(
            _visible_share_for_record(records.get(slot), root_rect) for slot in inactive_slots
        )
        inactive_tax_score_raw = _score_inactive_tax_value(
            inactive_tax, max_inactive_tax
        )
        inactive_tax_score = round(inactive_tax_score_raw)

        trigger_scores: list[tuple[float, float]] = []
        trigger_scores_raw: list[tuple[float, float]] = []
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
                    trigger_scores_raw.append((0.0, 0.35))
                elif realization != "compact-trigger":
                    panel_like_triggers.append(slot)
                    trigger_scores.append((40, 0.35))
                    trigger_scores_raw.append((40.0, 0.35))
                elif share >= 0.0025 and share <= max(
                    0.035, max_inactive_tax * 0.75
                ):
                    trigger_scores.append((100, 0.35))
                    trigger_scores_raw.append((100.0, 0.35))
                elif share > max(0.035, max_inactive_tax * 0.75):
                    panel_like_triggers.append(slot)
                    trigger_scores.append((58, 0.35))
                    trigger_scores_raw.append((58.0, 0.35))
                else:
                    trigger_scores.append((64, 0.35))
                    trigger_scores_raw.append((64.0, 0.35))

        required_score_raw = (
            _phase_score_value(required_parts_raw) if required_parts_raw else 100.0
        )
        required_score = round(required_score_raw)
        active_support_score_raw = (
            _phase_score_value(active_support_parts_raw)
            if active_support_parts_raw
            else 100.0
        )
        active_support_score = round(active_support_score_raw)
        trigger_score_raw = (
            _phase_score_value(trigger_scores_raw) if trigger_scores_raw else 100.0
        )
        trigger_score = round(trigger_score_raw)
        classification = measurement.get("classification") or {}
        geometry_score = int(
            classification.get("geometryScore", classification.get("score", 100)) or 0
        )
        phase_score_raw = _phase_score_value(
            [
                (dominant_score_raw, 1.45),
                (required_score_raw, 1.05),
                (inactive_tax_score_raw, 1.20),
                (active_support_score_raw, 0.90),
                (trigger_score_raw, 0.45),
                (float(geometry_score), 1.00),
            ]
        )
        phase_score = round(phase_score_raw)

        facts = measurement.get("geometryFacts") or {}
        clipped_count = int(facts.get("clippedCriticalControlCount", 0) or 0)
        hidden_count = int(facts.get("hiddenCriticalControlCount", 0) or 0)
        blocked_count = int(facts.get("blockedCriticalControlCount", 0) or 0)
        partition_overlap = float(
            facts.get(
                "undeclaredPartitionOverlapShare",
                facts.get("undeclaredPartitionOverlapShareShadow", 0),
            )
            or 0
        )
        overlay_budget_count = len(
            ((facts.get("paintedOwnership") or facts.get("paintedOwnershipShadow") or {}).get(
                "overlayBudgetExceeded", []
            ))
        )
        phase_hard_failures: list[str] = []
        if not dominant_floor_gate["met"]:
            phase_hard_failures.append(
                phase_share_floor_failure_reason(
                    phase,
                    dominant_slot,
                    dominant_floor_gate,
                )
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
        if blocked_count:
            phase_hard_failures.append(
                f"{phase} phase has {blocked_count} foreign-intercepted active critical control(s)"
            )
        if partition_overlap > 0.002:
            phase_hard_failures.append(
                f"{phase} phase has {partition_overlap:.1%} undeclared partition overlap"
            )
        if overlay_budget_count:
            phase_hard_failures.append(
                f"{phase} phase exceeds {overlay_budget_count} declared overlay budget(s)"
            )

        if phase_hard_failures:
            phase_score_raw = min(phase_score_raw, 68.0)
            phase_score = min(round(phase_score_raw), 68)
        hard_failures.extend(phase_hard_failures)
        risks = list(phase_hard_failures)
        if risks:
            reason = risks[0]
        elif active_support_slots:
            reason = (
                f"{phase} phase keeps {dominant_slot} at {dominant_share:.0%} "
                f"({dominant_headroom:+.1%} floor headroom), opens "
                f"{', '.join(active_support_slots)}, and limits inactive support tax to "
                f"{inactive_tax:.0%}"
            )
        elif inactive_slots:
            reason = (
                f"{phase} phase keeps {dominant_slot} at {dominant_share:.0%} "
                f"({dominant_headroom:+.1%} floor headroom) and replaces "
                f"{len(inactive_slots)} inactive support surface(s) with compact trigger(s)"
            )
        else:
            reason = (
                f"{phase} phase keeps {dominant_slot} at {dominant_share:.0%} "
                f"({dominant_headroom:+.1%} floor headroom) with required context visible"
            )

        weighted_total += phase_score * phase_weight
        weighted_raw_total += phase_score_raw * phase_weight
        weight_total += phase_weight
        evaluated.append(
            {
                "phase": phase,
                "score": int(phase_score),
                "rawScore": float(phase_score_raw),
                "weight": phase_weight,
                "geometryScore": geometry_score,
                "dominantSlot": dominant_slot,
                "dominantShare": dominant_share,
                "dominantRealization": dominant_state,
                "minDominantShare": min_dominant,
                "targetDominantShare": target_dominant,
                "dominantFloorMet": bool(dominant_floor_gate["met"]),
                "dominantFloorTolerance": float(dominant_floor_gate["tolerance"]),
                "dominantFloorShortfall": float(dominant_floor_gate["shortfall"]),
                "dominantRawHeadroom": dominant_raw_headroom,
                "dominantHeadroom": dominant_headroom,
                "dominantTargetDelta": dominant_target_delta,
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
                "blockedCriticalControlCount": blocked_count,
                "undeclaredPartitionOverlapShare": partition_overlap,
                "overlayBudgetExceededCount": overlay_budget_count,
                "hardFailure": bool(phase_hard_failures),
                "hardFailureReasons": phase_hard_failures,
                "reason": reason,
                "risks": risks,
                "snapshots": measurement.get("snapshots", {}),
            }
        )

    mean_score_raw = weighted_raw_total / weight_total if weight_total else 0.0
    mean_score = round(weighted_total / weight_total) if weight_total else 0
    worst_score_raw = min(
        (float(item.get("rawScore", item["score"])) for item in evaluated),
        default=0.0,
    )
    worst_score = min((item["score"] for item in evaluated), default=0)
    selected_phase = next(
        (
            name
            for name in ("selected-project-default", "default")
            if any(item["phase"] == name for item in evaluated)
        ),
        evaluated[0]["phase"] if evaluated else "",
    )
    selected_default = next(
        (item for item in evaluated if item["phase"] == selected_phase),
        {},
    )
    selected_default_score = int(selected_default.get("score", 0) or 0)
    selected_default_score_raw = float(
        selected_default.get("rawScore", selected_default_score) or 0.0
    )
    policy_score_raw = (
        (0.50 * worst_score_raw)
        + (0.30 * selected_default_score_raw)
        + (0.20 * mean_score_raw)
        if evaluated
        else 0.0
    )
    policy_score = round(policy_score_raw)
    phase_raw_scores = [
        float(item.get("rawScore", item.get("score", 0)) or 0.0)
        for item in evaluated
    ]
    score_variance = (
        sum((score - mean_score_raw) ** 2 for score in phase_raw_scores)
        / len(phase_raw_scores)
        if phase_raw_scores
        else 0.0
    )
    dominant_headrooms = [
        float(item.get("dominantHeadroom", 0.0) or 0.0)
        for item in evaluated
    ]
    raw_dominant_headrooms = [
        float(item.get("dominantRawHeadroom", item.get("dominantHeadroom", 0.0)) or 0.0)
        for item in evaluated
    ]
    floor_failure_count = sum(
        1 for item in evaluated if not bool(item.get("dominantFloorMet", False))
    )
    worst_dominant_headroom = min(dominant_headrooms, default=-1.0)
    worst_raw_dominant_headroom = min(raw_dominant_headrooms, default=-1.0)
    mean_dominant_headroom = (
        sum(dominant_headrooms) / len(dominant_headrooms)
        if dominant_headrooms
        else -1.0
    )
    selected_default_headroom = float(
        selected_default.get("dominantHeadroom", -1.0) or 0.0
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
        "policyScoreRaw": float(policy_score_raw),
        "meanScore": int(mean_score),
        "meanScoreRaw": float(mean_score_raw),
        "worstScore": int(worst_score),
        "worstScoreRaw": float(worst_score_raw),
        "selectedDefaultPhase": selected_phase,
        "selectedDefaultScore": int(selected_default_score),
        "selectedDefaultScoreRaw": float(selected_default_score_raw),
        "scoreVariance": float(score_variance),
        "scoreStdDev": float(math.sqrt(score_variance)),
        "phaseFloorTolerance": PHASE_SHARE_FLOOR_TOLERANCE,
        "phaseFloorFailureCount": int(floor_failure_count),
        "worstDominantHeadroom": float(worst_dominant_headroom),
        "worstRawDominantHeadroom": float(worst_raw_dominant_headroom),
        "meanDominantHeadroom": float(mean_dominant_headroom),
        "selectedDefaultHeadroom": float(selected_default_headroom),
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
            "hard phase failures remain absolute. Effective dominant share is compared "
            "directly with the declared phase floor using the shared subpixel tolerance."
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
        (66 if aligned else 28)
        + _target_closeness_score(unit_share, 0.05, 0.05) * 0.30
        + 4
    )
    return (
        min(100, score),
        f"footer overlay keeps feedback aligned in {unit_share:.0%} of exclusively owned root area; declared overlap={overlaps_focus}",
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
    top_ratio = (
        rect["top"] - root_rect["top"]
    ) / max(1.0, root_rect["height"])
    bottom_gap_ratio = (
        root_rect["bottom"] - rect["bottom"]
    ) / max(1.0, root_rect["height"])

    active_slot = next(iter(expected), "")
    active_role = str((records.get(active_slot) or {}).get("role") or "support")
    if policy == "bounded-side-drawer":
        target = 0.15 if active_role == "evidence" else 0.10
        floor = 0.12 if active_role == "evidence" else 0.075
        orientation = width_ratio <= 0.30 and height_ratio >= 0.45
        score = round(
            (58 if orientation else 24)
            + _target_closeness_score(share, target, 0.10) * 0.38
        )
        return (
            min(100, score),
            f"{policy} gives active support {share:.0%} of root as an exclusive side partition",
            not orientation or share < floor,
        )

    if policy in {"tabbed-phase-support", "user-tab-workbench"}:
        stage_ok = width_ratio >= 0.72 and height_ratio >= 0.48
        floor = 0.38
        score = round(
            (62 if stage_ok else 24)
            + _target_closeness_score(share, 0.58, 0.30) * 0.34
        )
        return (
            min(100, score),
            f"{policy} gives the active support tab {share:.0%} of root with a separate summary row",
            not stage_ok or share < floor,
        )

    if policy == "sequential-phase-stage":
        stage_ok = width_ratio >= 0.76 and height_ratio >= 0.56
        floor = 0.48
        score = round(
            (64 if stage_ok else 22)
            + _target_closeness_score(share, 0.66, 0.28) * 0.34
        )
        return (
            min(100, score),
            f"{policy} gives the active phase stage {share:.0%} of root and preserves a returnable summary",
            not stage_ok or share < floor,
        )

    target = 0.17 if active_role == "evidence" else 0.13
    floor = 0.12 if active_role == "evidence" else 0.08
    horizontal = width_ratio >= 0.72 and height_ratio <= 0.34

    if policy == "bounded-bottom-drawer":
        placement_ok = bottom_gap_ratio <= 0.12
        placement_text = "bottom-most drawer row"
    elif policy == "inline-phase-stage":
        placement_ok = top_ratio <= 0.34
        placement_text = "pre-workflow inline stage"
    else:
        # one-active-plus-triggers uses a neutral stage between the dominant work
        # and the persistent feedback band; it must not alias the side drawer.
        placement_ok = 0.45 <= top_ratio <= 0.86 and bottom_gap_ratio >= 0.03
        placement_text = "neutral phase stage"

    orientation_score = 50 if horizontal else 20
    placement_score = 12 if placement_ok else 0
    score = round(
        orientation_score
        + placement_score
        + _target_closeness_score(share, target, 0.12) * 0.38
    )
    return (
        min(100, score),
        f"{policy} gives active support {share:.0%} of root as an exclusive {placement_text}",
        not horizontal or not placement_ok or share < floor,
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
    blocked = list(examples.get("blockedCriticalControls") or [])
    critical_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in [*clipped, *hidden, *blocked]:
        unit_id = str(issue.get("unitId") or "")
        if not unit_id:
            control_owner = str(issue.get("controlOwner") or "")
            owner_record = records.get(control_owner)
            unit_id = str((owner_record or {}).get("unitId") or "")
        if unit_id:
            critical_by_unit[unit_id].append(issue)
    ownership = (
        (measurement.get("geometryFacts") or {}).get("paintedOwnership")
        or (measurement.get("geometryFacts") or {}).get("paintedOwnershipShadow")
        or {}
    )
    partition_overlap_by_unit = {
        str(item.get("unitId") or ""): float(item.get("shareOfRoot", 0) or 0)
        for item in ownership.get("partitionOverlapByUnit", [])
        if item.get("unitId")
    }

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
                f"{unit_id} has {len(critical_issues)} clipped, hidden, or foreign-intercepted active critical control(s)"
            )
            parts.append((0, 0.25))
        else:
            parts.append((100, 0.25))
        partition_overlap_share = partition_overlap_by_unit.get(unit_id, 0.0)
        if partition_overlap_share > 0.002:
            local_hard.append(
                f"{unit_id} paints over {partition_overlap_share:.1%} of foreign partition space"
            )
            parts.append((0, 0.20))
        else:
            parts.append((100, 0.20))

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
            "tabbed-phase-support",
            "user-tab-workbench",
            "sequential-phase-stage",
            "side-active-support",
            "narrow-side-active-support",
            "proof-drawer-or-side-support",
        }:
            normalized_policy = {
                "side-active-support": "bounded-side-drawer",
                "narrow-side-active-support": "bounded-side-drawer",
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

        score_raw = _phase_score_value(parts)
        score = round(score_raw)
        hard_failures.extend(local_hard)
        results.append(
            {
                "unitId": unit_id,
                "role": unit.get("role", "support"),
                "policy": policy,
                "path": unit.get("path", []),
                "score": score,
                "rawScore": float(score_raw),
                "hardFailureCount": len(local_hard),
                "hardFailureReasons": local_hard,
                "activeSlots": active_slots,
                "triggerSlots": trigger_slots,
                "criticalIssueCount": len(critical_issues),
                "partitionOverlapShare": partition_overlap_share,
                "reasons": reasons,
            }
        )

    scores = [item["score"] for item in results]
    raw_scores = [float(item.get("rawScore", item["score"])) for item in results]
    worst = min(scores) if scores else 100
    worst_raw = min(raw_scores) if raw_scores else 100.0
    mean_raw = sum(raw_scores) / len(raw_scores) if raw_scores else 100.0
    mean = round(sum(scores) / len(scores)) if scores else 100
    score_raw = (worst_raw * 0.55) + (mean_raw * 0.45)
    score = round(score_raw)
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
        "rawScore": float(score_raw),
        "state": state,
        "worstScore": worst,
        "worstScoreRaw": float(worst_raw),
        "meanScore": mean,
        "meanScoreRaw": float(mean_raw),
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
    raw_scores = [
        float(item.get("rawScore", item.get("score", 0)) or 0.0)
        for item in phase_fits
    ]
    worst = min(scores) if scores else 0
    worst_raw = min(raw_scores) if raw_scores else 0.0
    mean_raw = sum(raw_scores) / len(raw_scores) if raw_scores else 0.0
    mean = round(sum(scores) / len(scores)) if scores else 0
    selected_phase = str(
        canonical_phase_scenario(hierarchy).get("phase") or "default"
    )
    selected = next(
        (item for item in phase_fits if item["phase"] == selected_phase),
        phase_fits[0] if phase_fits else {"score": 0},
    )
    selected_score = int(selected.get("score", 0) or 0)
    selected_score_raw = float(
        selected.get("rawScore", selected_score) or 0.0
    )
    score_raw = (
        (worst_raw * 0.50)
        + (selected_score_raw * 0.30)
        + (mean_raw * 0.20)
    )
    score = round(score_raw)
    hard_failures = list(
        dict.fromkeys(
            reason
            for fit in phase_fits
            for reason in (fit.get("hardFailureReasons") or [])
        )
    )

    branch_scores: dict[str, list[int]] = defaultdict(list)
    branch_raw_scores: dict[str, list[float]] = defaultdict(list)
    branch_hard: dict[str, int] = defaultdict(int)
    for fit in phase_fits:
        for unit in fit.get("units") or []:
            unit_id = unit["unitId"]
            branch_scores[unit_id].append(int(unit.get("score", 0) or 0))
            branch_raw_scores[unit_id].append(
                float(unit.get("rawScore", unit.get("score", 0)) or 0.0)
            )
            branch_hard[unit_id] += int(unit.get("hardFailureCount", 0) or 0)
    parallel = [
        {
            "unitId": unit_id,
            "worstScore": min(values),
            "worstScoreRaw": min(branch_raw_scores[unit_id]),
            "meanScore": round(sum(values) / len(values)),
            "meanScoreRaw": (
                sum(branch_raw_scores[unit_id])
                / len(branch_raw_scores[unit_id])
            ),
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
        "rawScore": float(score_raw),
        "state": state,
        "worstScore": worst,
        "worstScoreRaw": float(worst_raw),
        "meanScore": mean,
        "meanScoreRaw": float(mean_raw),
        "selectedDefaultScore": selected_score,
        "selectedDefaultScoreRaw": float(selected_score_raw),
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
        selection_score_raw = (
            (geometry_score * 0.34)
            + (float(fit.get("rawScore", fit["score"])) * 0.16)
            + (float(affordance_fit.get("rawScore", affordance_fit["score"])) * 0.13)
            + (float(phase_fit.get("policyScoreRaw", phase_fit["score"])) * 0.20)
            + (float(unit_fit.get("rawScore", unit_fit["score"])) * 0.17)
        )
    else:
        selection_score_raw = (
            (geometry_score * 0.45)
            + (float(fit.get("rawScore", fit["score"])) * 0.22)
            + (float(affordance_fit.get("rawScore", affordance_fit["score"])) * 0.18)
            + (float(phase_fit.get("policyScoreRaw", phase_fit["score"])) * 0.15)
        )
    selection_score = round(selection_score_raw)

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
    classification["phaseFloorTolerance"] = phase_fit.get(
        "phaseFloorTolerance", PHASE_SHARE_FLOOR_TOLERANCE
    )
    classification["phaseFloorFailureCount"] = int(
        phase_fit.get("phaseFloorFailureCount", 0) or 0
    )
    classification["phaseHardFailureCount"] = phase_hard_failure_count
    classification["layoutUnitFitScore"] = unit_fit["score"]
    classification["layoutUnitFitState"] = unit_fit["state"]
    classification["layoutUnitWorstScore"] = unit_fit.get("worstScore", 100)
    classification["layoutUnitHardFailureCount"] = unit_hard_failure_count
    classification["selectionScore"] = selection_score
    classification["selectionScoreRaw"] = float(selection_score_raw)
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
        responsive_contract: dict[str, Any] | None = None,
        layout_hint_source: dict[str, Any] | None = None,
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
            "responsiveContract": copy.deepcopy(responsive_contract or {}),
            "layoutHintSource": copy.deepcopy(layout_hint_source or {}),
        }
        # Fail at fixture construction time rather than during browser rendering.
        layout_unit_specs(result)
        if result["layoutHintSource"]:
            normalized_hints = normalize_layout_hint_contract(result)
            if normalized_hints["state"] != "complete":
                raise ValueError(
                    f"Hierarchy {id} declares invalid layout hints: "
                    + "; ".join(normalized_hints["issues"])
                )
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
            responsive_contract={
                "mode": "derived-from-layout-hints",
                "policyVersion": RESPONSIVE_POLICY_VERSION,
                "bands": [
                    {
                        "id": "wide",
                        "minWidth": 1320,
                        "maxRemediationLevel": 0,
                        "reason": "Two large active regions can remain simultaneously partitioned.",
                    },
                    {
                        "id": "medium",
                        "minWidth": 1040,
                        "maxRemediationLevel": 1,
                        "reason": "Compact rails or a bottom partition become acceptable.",
                    },
                    {
                        "id": "narrow",
                        "minWidth": 760,
                        "maxRemediationLevel": 2,
                        "reason": "Inline stages or tightly budgeted overlays become acceptable.",
                    },
                    {
                        "id": "compact",
                        "minWidth": 0,
                        "maxRemediationLevel": 3,
                        "reason": "Sequential active-stage replacement is allowed when co-presence is infeasible.",
                    },
                ],
                "minimumRobustHeadroom": 0.01,
                "switchPenalty": 1.75,
                "unnecessaryRemediationPenalty": 12.0,
                "hysteresisPx": DEFAULT_RESPONSIVE_HYSTERESIS_PX,
                "transitionProofPlans": [
                    {
                        "fromPlacement": "right",
                        "toPlacement": "bottom",
                        "upperWidth": 1520,
                        "lowerWidth": 1320,
                        "maxDepth": 3,
                        "requiredPositiveOverlapPx": DEFAULT_RESPONSIVE_HYSTERESIS_PX,
                        "bracketEvidence": {
                            "center": "both realizations pass near the authored 1440px boundary",
                            "purpose": "measure the complete positive-headroom overlap envelope",
                        },
                    },
                    {
                        "fromPlacement": "bottom",
                        "toPlacement": "tab",
                        "upperWidth": 1060,
                        "lowerWidth": 920,
                        "maxDepth": 3,
                        "requiredPositiveOverlapPx": DEFAULT_RESPONSIVE_HYSTERESIS_PX,
                        "bracketEvidence": {
                            "upper": "bottom passes while tab approaches its identity-stage floor",
                            "lower": "tab passes while bottom approaches its workflow floor",
                        },
                    },
                    {
                        "fromPlacement": "tab",
                        "toPlacement": "stage",
                        "upperWidth": 800,
                        "lowerWidth": 640,
                        "maxDepth": 3,
                        "requiredPositiveOverlapPx": DEFAULT_RESPONSIVE_HYSTERESIS_PX,
                        "bracketEvidence": {
                            "center": "both realizations pass near the authored 720px boundary",
                            "purpose": "measure the complete positive-headroom overlap envelope",
                        },
                    },
                ],
                "semanticInvariants": [
                    "project identity remains available",
                    "active workflow phase remains unambiguous",
                    "critical actions remain reachable",
                    "status remains attributable to the selected project",
                    "active evidence or recovery support remains reachable",
                ],
                "phasePresentations": {
                    "medium": {
                        "project-selection": {
                            "presentationMode": "identity-partition",
                            "minDominantShare": 0.22,
                            "targetDominantShare": 0.30,
                        },
                        "selected-project-default": {
                            "presentationMode": "workflow-only",
                            "minDominantShare": 0.54,
                            "targetDominantShare": 0.64,
                        },
                        "planning": {
                            "presentationMode": "workflow-with-bottom-support",
                            "minDominantShare": 0.40,
                            "targetDominantShare": 0.50,
                        },
                        "execution": {
                            "presentationMode": "workflow-with-bottom-support",
                            "minDominantShare": 0.40,
                            "targetDominantShare": 0.50,
                        },
                        "proof-review": {
                            "presentationMode": "workflow-with-bottom-support",
                            "minDominantShare": 0.38,
                            "targetDominantShare": 0.48,
                        },
                        "recovery": {
                            "presentationMode": "workflow-with-bottom-support",
                            "minDominantShare": 0.38,
                            "targetDominantShare": 0.48,
                        },
                    },
                    "narrow": {
                        "project-selection": {
                            "presentationMode": "identity-stage",
                            "minDominantShare": 0.24,
                            "targetDominantShare": 0.34,
                        },
                        "selected-project-default": {
                            "presentationMode": "workflow-tab",
                            "dominantSlot": "workflow",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command"],
                            "activeSupportSlots": [],
                            "collapsedSlots": [
                                "project-selector",
                                "server",
                                "evidence",
                                "advanced",
                            ],
                            "reachableSlots": ["server", "evidence", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.48,
                            "targetDominantShare": 0.58,
                            "reasonSuffix": "The command surface is replaced by a compact summary while workflow owns the active tab.",
                        },
                        "planning": {
                            "presentationMode": "support-tab",
                            "dominantSlot": "server",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command", "workflow"],
                            "activeSupportSlots": ["server"],
                            "collapsedSlots": ["project-selector", "evidence", "advanced"],
                            "reachableSlots": ["workflow", "evidence", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.46,
                            "targetDominantShare": 0.60,
                            "reasonSuffix": "Narrow capacity makes server the active tab while workflow remains as a compact returnable summary.",
                        },
                        "execution": {
                            "presentationMode": "workflow-tab",
                            "dominantSlot": "workflow",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command"],
                            "activeSupportSlots": [],
                            "collapsedSlots": ["project-selector", "server", "evidence", "advanced"],
                            "reachableSlots": ["evidence"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.46,
                            "targetDominantShare": 0.58,
                            "reasonSuffix": "Execution keeps workflow active, replaces command controls with a compact summary, and makes evidence reachable through the support tab strip.",
                        },
                        "proof-review": {
                            "presentationMode": "support-tab",
                            "dominantSlot": "evidence",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["workflow"],
                            "activeSupportSlots": ["evidence"],
                            "collapsedSlots": ["project-selector", "server", "advanced"],
                            "reachableSlots": ["workflow", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.48,
                            "targetDominantShare": 0.62,
                            "reasonSuffix": "Proof becomes the active tab while workflow context remains visible as a summary.",
                        },
                        "recovery": {
                            "presentationMode": "support-tab",
                            "dominantSlot": "advanced",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["workflow"],
                            "activeSupportSlots": ["advanced"],
                            "collapsedSlots": ["project-selector", "server", "evidence"],
                            "reachableSlots": ["workflow", "evidence"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.48,
                            "targetDominantShare": 0.62,
                            "reasonSuffix": "Recovery becomes the active tab while workflow context remains visible as a summary.",
                        },
                    },
                    "user-tab-workbench": {
                        "project-selection": {
                            "presentationMode": "user-tab-identity-stage",
                            "minDominantShare": 0.24,
                            "targetDominantShare": 0.34,
                        },
                        "selected-project-default": {
                            "presentationMode": "user-tab-workflow",
                            "dominantSlot": "workflow",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command"],
                            "activeSupportSlots": [],
                            "collapsedSlots": [
                                "project-selector",
                                "server",
                                "evidence",
                                "advanced",
                            ],
                            "reachableSlots": ["server", "evidence", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.48,
                            "targetDominantShare": 0.58,
                            "reasonSuffix": "The user-authored tab workbench keeps workflow active and preserves the current command as a semantic summary.",
                        },
                        "planning": {
                            "presentationMode": "user-tab-support",
                            "dominantSlot": "server",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command", "workflow"],
                            "activeSupportSlots": ["server"],
                            "collapsedSlots": ["project-selector", "evidence", "advanced"],
                            "reachableSlots": ["workflow", "evidence", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.46,
                            "targetDominantShare": 0.60,
                            "reasonSuffix": "The user-authored server tab preserves current command and workflow context at their required companion floors.",
                        },
                        "execution": {
                            "presentationMode": "user-tab-workflow",
                            "dominantSlot": "workflow",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command"],
                            "activeSupportSlots": [],
                            "collapsedSlots": [
                                "project-selector",
                                "server",
                                "evidence",
                                "advanced",
                            ],
                            "reachableSlots": ["evidence"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.46,
                            "targetDominantShare": 0.58,
                            "reasonSuffix": "The user-authored tab workbench keeps execution in workflow and makes evidence reachable.",
                        },
                        "proof-review": {
                            "presentationMode": "user-tab-support",
                            "dominantSlot": "evidence",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command", "workflow"],
                            "activeSupportSlots": ["evidence"],
                            "collapsedSlots": ["project-selector", "server", "advanced"],
                            "reachableSlots": ["workflow", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.48,
                            "targetDominantShare": 0.62,
                            "reasonSuffix": "The user-authored evidence tab preserves current operation and workflow state with an explicit return action.",
                        },
                        "recovery": {
                            "presentationMode": "user-tab-support",
                            "dominantSlot": "advanced",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command", "workflow"],
                            "activeSupportSlots": ["advanced"],
                            "collapsedSlots": ["project-selector", "server", "evidence"],
                            "reachableSlots": ["workflow", "evidence"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.48,
                            "targetDominantShare": 0.62,
                            "reasonSuffix": "The user-authored recovery tab preserves current operation and workflow state with an explicit return action.",
                        },
                    },
                    "compact": {
                        "project-selection": {
                            "presentationMode": "identity-stage",
                            "minDominantShare": 0.30,
                            "targetDominantShare": 0.42,
                        },
                        "selected-project-default": {
                            "presentationMode": "workflow-stage",
                            "dominantSlot": "workflow",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command"],
                            "activeSupportSlots": [],
                            "collapsedSlots": [
                                "project-selector",
                                "server",
                                "evidence",
                                "advanced",
                            ],
                            "reachableSlots": ["server", "evidence", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.44,
                            "targetDominantShare": 0.58,
                            "reasonSuffix": "Compact default replaces the full command payload with a returnable summary.",
                        },
                        "planning": {
                            "presentationMode": "sequential-support-stage",
                            "dominantSlot": "server",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["workflow"],
                            "activeSupportSlots": ["server"],
                            "collapsedSlots": ["project-selector", "evidence", "advanced", "command"],
                            "reachableSlots": ["workflow", "evidence", "advanced"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.52,
                            "targetDominantShare": 0.68,
                            "reasonSuffix": "Compact capacity gives server the full active stage and preserves workflow as a returnable summary.",
                        },
                        "execution": {
                            "presentationMode": "workflow-stage",
                            "dominantSlot": "workflow",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["command"],
                            "activeSupportSlots": [],
                            "collapsedSlots": ["project-selector", "server", "evidence", "advanced"],
                            "reachableSlots": ["evidence"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.48,
                            "targetDominantShare": 0.62,
                            "reasonSuffix": "Compact execution keeps workflow active and evidence reachable through a trigger.",
                        },
                        "proof-review": {
                            "presentationMode": "sequential-support-stage",
                            "dominantSlot": "evidence",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["workflow"],
                            "activeSupportSlots": ["evidence"],
                            "collapsedSlots": ["project-selector", "server", "advanced", "command"],
                            "reachableSlots": ["workflow"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.54,
                            "targetDominantShare": 0.70,
                            "reasonSuffix": "Compact proof review gives evidence the active stage and preserves a workflow return path.",
                        },
                        "recovery": {
                            "presentationMode": "sequential-support-stage",
                            "dominantSlot": "advanced",
                            "requiredSlots": ["project-context", "status"],
                            "summarySlots": ["workflow"],
                            "activeSupportSlots": ["advanced"],
                            "collapsedSlots": ["project-selector", "server", "evidence", "command"],
                            "reachableSlots": ["workflow"],
                            "returnToSlot": "workflow",
                            "minDominantShare": 0.54,
                            "targetDominantShare": 0.70,
                            "reasonSuffix": "Compact recovery gives advanced controls the active stage and preserves a workflow return path.",
                        },
                    },
                },
            },
            layout_hint_source={
                "version": LAYOUT_HINT_CONTRACT_VERSION,
                "sourceKind": "synthetic-data-mc-layout-attributes",
                "rootUnitId": "git-tools-application",
                "attributes": {
                    "data-mc-layout-root": "git-tools-application",
                    "data-mc-layout": "dock-workbench",
                    "data-mc-layout-zones": (
                        "top left center right bottom tab stage trigger"
                    ),
                    "data-mc-layout-policy": "dominant-workflow-stack",
                    "data-mc-layout-capacity": "wide",
                },
                "units": {
                    "project-identity": {
                        "data-mc-layout-prefer": "left",
                        "data-mc-layout-allowed": "left top trigger",
                        "data-mc-layout-fallback": "top trigger",
                        "data-mc-layout-strength": "strong",
                        "data-mc-layout-policy": "phase-selector-unit",
                        "data-mc-layout-inactive": "trigger",
                        "data-mc-layout-min-inline": "220",
                        "data-mc-layout-min-block": "120",
                        "data-mc-layout-max-share": "0.24",
                        "data-mc-layout-user-id": "repository.project-identity",
                        "data-mc-layout-user-mutable": "placement collapsed",
                    },
                    "command-workflow": {
                        "data-mc-layout-prefer": "center",
                        "data-mc-layout-allowed": "center",
                        "data-mc-layout-fallback": "",
                        "data-mc-layout-strength": "required",
                        "data-mc-layout-policy": "command-inline-header",
                        "data-mc-layout-internal": "command-inline workflow-center",
                        "data-mc-layout-min-inline": "520",
                        "data-mc-layout-min-block": "360",
                        "data-mc-layout-max-share": "0.78",
                        "data-mc-layout-user-id": "repository.command-workflow",
                        "data-mc-layout-user-mutable": "",
                    },
                    "persistent-feedback": {
                        "data-mc-layout-prefer": "bottom",
                        "data-mc-layout-allowed": "bottom top",
                        "data-mc-layout-fallback": "top",
                        "data-mc-layout-strength": "strong",
                        "data-mc-layout-policy": "shared-horizontal-band",
                        "data-mc-layout-internal": "project-context status",
                        "data-mc-layout-min-inline": "420",
                        "data-mc-layout-min-block": "44",
                        "data-mc-layout-max-share": "0.10",
                        "data-mc-layout-user-id": "repository.persistent-feedback",
                        "data-mc-layout-user-mutable": "placement",
                    },
                    "phase-support": {
                        "data-mc-layout-prefer": "right",
                        "data-mc-layout-allowed": "right bottom tab stage trigger",
                        "data-mc-layout-fallback": "bottom tab stage trigger",
                        "data-mc-layout-strength": "preferred",
                        "data-mc-layout-policy": "bounded-side-drawer",
                        "data-mc-layout-inactive": "trigger",
                        "data-mc-layout-min-inline": "300",
                        "data-mc-layout-min-block": "260",
                        "data-mc-layout-max-share": "0.32",
                        "data-mc-layout-user-id": "repository.phase-support",
                        "data-mc-layout-user-mutable": "placement share collapsed tab-group",
                    },
                },
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
                            {
                                "policy": "tabbed-phase-support",
                                "alias": "support-tabs",
                                "preflightScore": 93,
                            },
                            {
                                "policy": "sequential-phase-stage",
                                "alias": "support-stage",
                                "preflightScore": 92,
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

    if realization == "compact-summary":
        attrs["class"] = f"flog-node flog-summary {base_class}"
        attrs["data-flog-min-visible-share"] = "0.008"
        attrs["data-flog-summary-for"] = slot
        attrs["data-flog-summary-slot"] = slot
        attrs["data-mc-layout-slot"] = "compact-summary"
        attrs["id"] = f"flog-summary-{slugify(slot)}"
        attr_text = " ".join(
            f'{name}="{html.escape(str(value), quote=True)}"'
            for name, value in attrs.items()
        )
        summary_items = list(node.get("items") or [])
        summary_label = (
            str(summary_items[0].get("label") or "")
            if summary_items
            else str(node.get("summaryReason") or "")
        )
        return_target = str(node.get("summaryReturnTo") or "")
        return_markup = (
            f'<button type="button" class="summary-return" '
            f'data-flog-return-to="{html.escape(return_target, quote=True)}">'
            f'Return to {html.escape(return_target)}</button>'
            if return_target
            else ""
        )
        return (
            f"<section {attr_text}>"
            f"<header class=\"node-header\"><h2>{html.escape(node['title'])}</h2>"
            "<span>compact summary</span></header>"
            f"<div class=\"summary-body\"><span>{html.escape(summary_label)}</span>"
            f"{return_markup}</div></section>"
        )

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
    user_layout_mutation = (
        copy.deepcopy(candidate.get("userLayoutMutation") or {})
        if isinstance(candidate, dict)
        else {}
    )
    preferred_share = max(
        0.0,
        min(0.45, float(user_layout_mutation.get("preferredShare", 0) or 0)),
    )
    user_tab_workbench = bool(
        user_layout_mutation.get("userTabWorkbench", False)
    )
    summary_minimums = dict(
        user_layout_mutation.get("summaryMinimumShares") or {}
    )
    user_layout_style_parts: list[str] = []
    if preferred_share > 0:
        user_layout_style_parts.extend(
            [
                f"--flog-user-support-fr:{preferred_share * 100:.4f}fr;",
                f"--flog-user-main-fr:{(1.0 - preferred_share) * 100:.4f}fr;",
                f"--flog-user-support-percent:{preferred_share * 100:.4f}%;",
            ]
        )
    if user_tab_workbench:
        requested_root = dict(
            summary_minimums.get("requestedRootShares") or {}
        )
        allocated_root = dict(
            summary_minimums.get("allocatedRootShares") or {}
        )
        resolved_local = dict(
            summary_minimums.get("resolvedLocalShares") or {}
        )
        requested_track_root = float(
            summary_minimums.get("requestedTrackRootShare", 0.10) or 0.10
        )
        workflow_required_root = float(
            requested_root.get("workflow", 0.055) or 0.055
        )
        command_required_root = float(
            requested_root.get("command", 0.026) or 0.026
        )
        workflow_allocated_root = float(
            allocated_root.get("workflow", workflow_required_root + 0.004)
            or workflow_required_root
        )
        command_allocated_root = float(
            allocated_root.get("command", command_required_root + 0.004)
            or command_required_root
        )
        # These scaffold shares exist only so Chromium can realize and measure
        # the nested parent.  USER_TAB_WORKBENCH_CALIBRATION_JS replaces them
        # with values derived from the actual rendered geometry before scoring.
        workflow_local = float(
            resolved_local.get(
                "workflow",
                workflow_allocated_root / max(0.001, requested_track_root),
            )
            or 0
        )
        command_local = float(
            resolved_local.get(
                "command",
                command_allocated_root / max(0.001, requested_track_root),
            )
            or 0
        )
        safety_local = max(0.0, 1.0 - workflow_local - command_local)
        user_layout_style_parts.extend(
            [
                "--flog-user-summary-parent-root-share:0%;",
                f"--flog-user-workflow-summary-required-root-share:{workflow_required_root * 100:.4f}%;",
                f"--flog-user-command-summary-required-root-share:{command_required_root * 100:.4f}%;",
                f"--flog-user-workflow-summary-allocated-root-block:{workflow_allocated_root * 100:.4f}vh;",
                f"--flog-user-command-summary-allocated-root-block:{command_allocated_root * 100:.4f}vh;",
                f"--flog-user-workflow-summary-local-share:{workflow_local * 100:.4f}%;",
                f"--flog-user-command-summary-local-share:{command_local * 100:.4f}%;",
                f"--flog-user-summary-safety-local-share:{safety_local * 100:.4f}%;",
                f"--flog-user-tab-summary-track-min-block:{requested_track_root * 100:.4f}vh;",
            ]
        )
    user_layout_style = "".join(user_layout_style_parts)
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
        "data-flog-phase-floor-tolerance": str(PHASE_SHARE_FLOOR_TOLERANCE),
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
        "data-flog-user-layout-proof": (
            "true" if user_layout_mutation else "false"
        ),
        "data-flog-user-layout-profile": str(
            user_layout_mutation.get("profileId") or ""
        ),
        "data-flog-user-layout-variant": str(
            user_layout_mutation.get("variant") or ""
        ),
        "data-flog-user-support-placement": str(
            user_layout_mutation.get("effectivePlacement") or ""
        ),
        "data-flog-user-preferred-share": (
            f"{preferred_share:.6f}" if preferred_share > 0 else ""
        ),
        "data-flog-user-collapsed-units": " ".join(
            user_layout_mutation.get("collapsedUnitIds") or []
        ),
        "data-flog-user-tab-with-unit": str(
            user_layout_mutation.get("tabWithUnitId") or ""
        ),
        "data-flog-user-tab-workbench": (
            "true" if user_tab_workbench else "false"
        ),
        "data-flog-user-summary-floor-source": str(
            summary_minimums.get("source") or ""
        ),
        "data-flog-user-summary-calibration-mode": str(
            summary_minimums.get("calibrationMode") or ""
        ),
        "data-flog-user-summary-parent-root-share": str(
            summary_minimums.get("parentEffectiveRootShare") or ""
        ),
        "data-flog-user-summary-requested-track-root-share": str(
            summary_minimums.get("requestedTrackRootShare") or ""
        ),
        "data-flog-user-summary-track-safety-root-share": str(
            summary_minimums.get("trackSafetyRootShare") or ""
        ),
        "data-flog-user-workflow-summary-required-root-share": str(
            (summary_minimums.get("requestedRootShares") or {}).get(
                "workflow", ""
            )
        ),
        "data-flog-user-command-summary-required-root-share": str(
            (summary_minimums.get("requestedRootShares") or {}).get(
                "command", ""
            )
        ),
        "data-flog-user-workflow-summary-allocated-root-share": str(
            (summary_minimums.get("allocatedRootShares") or {}).get(
                "workflow", ""
            )
        ),
        "data-flog-user-command-summary-allocated-root-share": str(
            (summary_minimums.get("allocatedRootShares") or {}).get(
                "command", ""
            )
        ),
        "data-flog-user-workflow-summary-local-share": str(
            (summary_minimums.get("resolvedLocalShares") or {}).get(
                "workflow", ""
            )
        ),
        "style": user_layout_style,
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


/* Stage B: exclusive painted ownership.  Partition policies reserve grid
   tracks; only policies explicitly declared as overlays may paint over another
   semantic unit. */
.flog-node[data-flog-role="command"] {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
}
.flog-node[data-flog-role="command"] > .node-header {
  flex: 0 0 min(132px, 28%);
  min-width: 0;
}
.flog-node[data-flog-role="command"] > .node-header span {
  display: none;
}
.flog-node[data-flog-role="command"] > .node-body {
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(0, 1fr);
  align-items: center;
  gap: 5px;
  overflow: hidden;
}
.flog-node[data-flog-role="command"] > .node-body > * {
  min-width: 0;
  max-width: 100%;
}
.flog-node[data-flog-role="command"] button,
.flog-node[data-flog-role="command"] input,
.flog-node[data-flog-role="command"] select,
.flog-node[data-flog-role="command"] textarea {
  min-width: 0;
  width: 100%;
  min-height: 30px;
  padding: 4px 6px;
  font-size: 11px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] {
  flex-direction: column;
  align-items: stretch;
  overflow: auto;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] > .node-header {
  flex: 0 0 auto;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] > .node-body {
  grid-auto-flow: row;
  grid-auto-rows: minmax(30px, auto);
  grid-auto-columns: auto;
  overflow: visible;
}

.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"] {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) auto;
  grid-template-areas:
    "main"
    "feedback";
  gap: var(--gap);
  position: relative;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="command-workflow"] {
  grid-area: main;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="persistent-feedback"] {
  grid-area: feedback;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"]:not(.unit-policy-selector-overlay) {
  grid-area: identity;
  position: static !important;
  inset: auto !important;
  width: auto !important;
  min-width: 0;
  min-height: 0;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  grid-area: support;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"]:not([data-flog-ownership-mode="overlay"]) {
  position: static !important;
  inset: auto !important;
  width: auto !important;
  height: auto !important;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  box-shadow: none;
}

/* Project selection is an exclusive identity/status split. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"]:not(.unit-policy-selector-overlay)
  ) {
  grid-template-columns: minmax(270px, 30fr) minmax(0, 70fr);
  grid-template-rows: minmax(0, 1fr);
  grid-template-areas: "identity feedback";
}
/* The full selector and compact rail are separate identity policies, not aliases. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-phase-selector-unit[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"]
  ) {
  grid-template-columns: minmax(300px, 33fr) minmax(0, 67fr);
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-compact-project-rail[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"]
  ) {
  grid-template-columns: minmax(210px, 21fr) minmax(0, 79fr);
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-compact-project-rail[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"]
  > .flog-node .node-body {
  grid-auto-flow: row;
  grid-auto-rows: minmax(30px, auto);
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"]:not(.unit-policy-selector-overlay)
  )
  > .flog-layout-unit[data-flog-unit-id="persistent-feedback"] {
  grid-area: feedback !important;
  position: static !important;
  inset: auto !important;
  width: auto !important;
  height: auto !important;
  margin: 0;
  align-self: stretch;
}

/* Side support receives only the width required by the active support role.
   Server planning remains compact; evidence receives enough proof space. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-active-support-slots~="server"]
  ) {
  grid-template-columns: minmax(0, 88fr) minmax(150px, 12fr);
  grid-template-rows: minmax(0, 1fr) auto;
  grid-template-areas:
    "main support"
    "feedback support";
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-active-support-slots~="evidence"]
  ) {
  grid-template-columns: minmax(0, 81fr) minmax(220px, 19fr);
  grid-template-rows: minmax(0, 1fr) auto;
  grid-template-areas:
    "main support"
    "feedback support";
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-active-support-slots~="advanced"]
  ) {
  grid-template-columns: minmax(0, 86fr) minmax(170px, 14fr);
  grid-template-rows: minmax(0, 1fr) auto;
  grid-template-areas:
    "main support"
    "feedback support";
}

/* Generated support policies must reserve genuinely different partition tracks.
   Fingerprints intentionally ignore policy names, so aliases cannot survive merely
   by carrying different metadata over the same rectangles. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-one-active-plus-triggers[data-flog-unit-has-active-support="true"]
  ) {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) minmax(110px, 14%) auto;
  grid-template-areas:
    "main"
    "support"
    "feedback";
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-bounded-bottom-drawer[data-flog-unit-has-active-support="true"]
  ) {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) auto minmax(112px, 15%);
  grid-template-areas:
    "main"
    "feedback"
    "support";
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-inline-phase-stage[data-flog-unit-has-active-support="true"]
  ) {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(120px, 16%) minmax(0, 1fr) auto;
  grid-template-areas:
    "support"
    "main"
    "feedback";
}


/* Milestone 3.2 user-layout browser proofs.  Semantic share preferences become
   bounded partition tracks; they never become absolute pixel coordinates. */
.flog-root[data-flog-user-layout-proof="true"][data-flog-user-support-placement="right"]:has(
  > .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"]
) {
  grid-template-columns:
    minmax(0, var(--flog-user-main-fr, 76fr))
    minmax(180px, var(--flog-user-support-fr, 24fr)) !important;
  grid-template-rows: minmax(0, 1fr) auto !important;
  grid-template-areas:
    "main support"
    "feedback support" !important;
}
.flog-root[data-flog-user-layout-proof="true"][data-flog-user-support-placement="bottom"]:has(
  > .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"]
) {
  grid-template-columns: minmax(0, 1fr) !important;
  grid-template-rows:
    minmax(0, 1fr)
    auto
    minmax(112px, var(--flog-user-support-percent, 24%)) !important;
  grid-template-areas:
    "main"
    "feedback"
    "support" !important;
}
/* Collapse remains phase-relative: the trigger is used only when project
   identity is not the active semantic surface. */
.flog-root[data-flog-user-layout-proof="true"][data-flog-user-collapsed-units~="project-identity"]
  .flog-trigger[data-flog-slot="project-selector"] {
  outline: 1px solid rgba(74, 116, 152, 0.45);
}

/* Milestone 2 responsive presentation primitives.  A compact summary is a
   semantic return/context surface, not a clipped copy of the full panel. */
.flog-node.flog-summary {
  min-height: 38px;
  height: auto;
  max-height: 54px;
  padding: 5px 8px;
  display: grid;
  grid-template-columns: minmax(110px, 0.28fr) minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  overflow: hidden;
}
.flog-node.flog-summary > .node-header {
  min-width: 0;
}
.flog-node.flog-summary > .node-header h2 {
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flog-node.flog-summary > .node-header span {
  display: none;
}
.flog-node.flog-summary > .summary-body {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
}
.flog-node.flog-summary > .summary-body > span {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flog-node.flog-summary .summary-return {
  flex: 0 0 auto;
  min-height: 28px;
  padding: 4px 8px;
}

/* A tab realization keeps exactly one large support surface active and reserves a
   compact semantic summary row for the displaced workflow. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit:is(.unit-policy-tabbed-phase-support, .unit-policy-user-tab-workbench)[data-flog-unit-has-active-support="true"]
  ) {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(72px, 10%) minmax(0, 1fr) auto;
  grid-template-areas:
    "main"
    "support"
    "feedback";
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit:is(.unit-policy-tabbed-phase-support, .unit-policy-user-tab-workbench)[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  position: relative !important;
  display: block;
  /* M2.6 trims the decorative tab strip before reducing active support area. */
  padding-top: 20px;
  border-top: 2px solid rgba(74, 116, 152, 0.55);
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit:is(.unit-policy-tabbed-phase-support, .unit-policy-user-tab-workbench)[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"]::before {
  content: "Active support tab";
  position: absolute;
  inset: 0 0 auto 0;
  height: 18px;
  padding: 2px 8px;
  background: rgba(219, 232, 245, 0.96);
  border-bottom: 1px solid rgba(74, 116, 152, 0.35);
  font-size: 10px;
  line-height: 13px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Tab and stage modes compact non-active chrome before taking space from the
   active stage.  Full command payloads have already been replaced by summaries. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit:is(.unit-policy-tabbed-phase-support, .unit-policy-user-tab-workbench)[data-flog-unit-has-active-support="true"]
  )
  > .flog-layout-unit[data-flog-unit-id="command-workflow"] {
  gap: 3px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit:is(
      .unit-policy-tabbed-phase-support,
      .unit-policy-user-tab-workbench,
      .unit-policy-sequential-phase-stage,
      .unit-policy-one-active-plus-triggers
    )[data-flog-unit-has-active-support="true"]
  )
  > .flog-layout-unit[data-flog-unit-id="persistent-feedback"] {
  min-height: 32px;
  gap: 4px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit:is(
      .unit-policy-tabbed-phase-support,
      .unit-policy-user-tab-workbench,
      .unit-policy-sequential-phase-stage,
      .unit-policy-one-active-plus-triggers
    )[data-flog-unit-has-active-support="true"]
  )
  > .flog-layout-unit[data-flog-unit-id="persistent-feedback"]
  > .flog-node {
  padding: 4px 6px;
  min-height: 30px;
}
/* M2.6 gives the narrow active tab a robust phase-floor margin by trimming
   only non-active feedback chrome.  The semantic feedback remains present. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit:is(.unit-policy-tabbed-phase-support, .unit-policy-user-tab-workbench)[data-flog-unit-has-active-support="true"]
  )
  > .flog-layout-unit[data-flog-unit-id="persistent-feedback"] {
  min-height: 28px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit:is(.unit-policy-tabbed-phase-support, .unit-policy-user-tab-workbench)[data-flog-unit-has-active-support="true"]
  )
  > .flog-layout-unit[data-flog-unit-id="persistent-feedback"]
  > .flog-node {
  min-height: 26px;
  padding-block: 2px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit:is(.unit-policy-tabbed-phase-support, .unit-policy-user-tab-workbench)[data-flog-unit-has-active-support="true"]
  )
  > .flog-layout-unit[data-flog-unit-id="command-workflow"]
  > .flog-node.flog-summary {
  min-height: 32px;
  max-height: 34px;
  padding-block: 3px;
}

/* User-authored tabbing is not the same as a narrow emergency tab.  Its
   command/workflow context track is derived from the same required-companion
   floors used by semantic phase scoring.  M3.6 keeps the root and local
   coordinate systems explicit, allocates concrete grid tracks, and requires
   each semantic summary node to fill the track that it owns. */
.has-layout-units.policy-phase-aware[data-flog-user-tab-workbench="true"]
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-user-tab-workbench[data-flog-unit-has-active-support="true"]
  ) {
  grid-template-rows:
    var(--flog-user-tab-summary-track-min-block, 10vh)
    minmax(0, 1fr)
    auto;
}
.has-layout-units.policy-phase-aware[data-flog-user-tab-workbench="true"]
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-user-tab-workbench[data-flog-unit-has-active-support="true"]
  )
  > .flog-layout-unit[data-flog-unit-id="command-workflow"]:has(
    > .flog-node.node-workflow.flog-summary
  ) {
  display: grid !important;
  grid-template-rows:
    minmax(
      var(--flog-user-command-summary-allocated-root-block, 3vh),
      var(--flog-user-command-summary-local-share, 18.75%)
    )
    minmax(
      var(--flog-user-workflow-summary-allocated-root-block, 5.9vh),
      var(--flog-user-workflow-summary-local-share, 36.875%)
    );
  align-content: start;
  align-items: stretch;
  gap: var(--gap);
}
.has-layout-units.policy-phase-aware[data-flog-user-tab-workbench="true"]
  .flog-layout-unit.unit-policy-user-tab-workbench[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  position: relative !important;
  display: block;
  padding-top: 20px;
  border-top: 2px solid rgba(74, 116, 152, 0.55);
}
.has-layout-units.policy-phase-aware[data-flog-user-tab-workbench="true"]
  .flog-layout-unit.unit-policy-user-tab-workbench[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"]::before {
  content: "User support tab";
}
.has-layout-units.policy-phase-aware[data-flog-user-tab-workbench="true"]
  .flog-layout-unit[data-flog-unit-id="command-workflow"]
  > .flog-node.flog-summary[data-flog-summary-slot="workflow"] {
  min-block-size: var(--flog-user-workflow-summary-allocated-root-block, 5.9vh) !important;
  block-size: auto !important;
  height: auto !important;
  max-block-size: none !important;
  max-height: none !important;
  align-self: stretch !important;
  grid-row: 2;
  order: 0;
}
.has-layout-units.policy-phase-aware[data-flog-user-tab-workbench="true"]
  .flog-layout-unit[data-flog-unit-id="command-workflow"]
  > .flog-node.flog-summary[data-flog-summary-slot="command"] {
  min-block-size: var(--flog-user-command-summary-allocated-root-block, 3vh) !important;
  block-size: auto !important;
  height: auto !important;
  max-block-size: none !important;
  max-height: none !important;
  align-self: stretch !important;
  display: grid !important;
  grid-template-columns: minmax(110px, 0.28fr) minmax(0, 1fr);
  align-items: center;
  grid-row: 1;
  order: 0;
}

/* A sequential stage gives the active phase surface the primary track and moves
   the workflow summary below it as an explicit return path. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-sequential-phase-stage[data-flog-unit-has-active-support="true"]
  ) {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) minmax(48px, 8%) auto;
  grid-template-areas:
    "support"
    "main"
    "feedback";
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-sequential-phase-stage[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  position: static !important;
  inset: auto !important;
  width: auto !important;
  height: auto !important;
  min-width: 0;
  min-height: 0;
}

/* Trigger-mode compact support uses the same exclusive stage ownership when the
   active support surface is the phase dominant; inactive support remains in the
   global trigger strip. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"]:has(
    > .flog-layout-unit.unit-policy-one-active-plus-triggers[data-flog-unit-has-active-support="true"]
      > .flog-node[data-flog-phase-dominant="true"]
  ) {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) minmax(76px, 14%) auto;
  grid-template-areas:
    "support"
    "main"
    "feedback";
}

/* Compact command/workflow summaries must not preserve desktop minimum heights. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="command-workflow"]:has(
    > .flog-node[data-flog-realization="compact-summary"]
  ) {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-realization="compact-summary"] {
  flex: 1 1 auto;
  min-height: 38px;
  height: auto;
}

/* A declared feedback overlay shares only the main grid cell. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-workflow-footer-overlay[data-flog-unit-id="persistent-feedback"] {
  grid-area: main;
  position: relative;
  inset: auto;
  align-self: end;
  z-index: 6;
  margin: 0 10px 10px;
  width: auto;
  height: 44px;
  min-height: 44px;
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
  .trial-workflow-with-proof-drawer {
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



USER_TAB_WORKBENCH_CALIBRATION_JS = r"""
() => {
  const root = document.querySelector(
    '.flog-root[data-flog-user-tab-workbench="true"]'
  );
  if (!root) {
    return {required: false, state: "not-applicable"};
  }
  const application = root.querySelector(
    '.flog-layout-unit[data-flog-unit-id="git-tools-application"]'
  );
  const parent = application && application.querySelector(
    ':scope > .flog-layout-unit[data-flog-unit-id="command-workflow"]'
  );
  const command = parent && parent.querySelector(
    ':scope > .flog-node[data-flog-summary-slot="command"]'
  );
  const workflow = parent && parent.querySelector(
    ':scope > .flog-node[data-flog-summary-slot="workflow"]'
  );
  if (!application || !parent || !command || !workflow) {
    return {
      required: false,
      state: "not-applicable",
      reason: "phase does not realize both command and workflow summary slots"
    };
  }

  const numberAttr = (name, fallback = 0) => {
    const value = Number.parseFloat(root.getAttribute(name) || "");
    return Number.isFinite(value) ? value : fallback;
  };
  const rectFacts = element => {
    const rect = element.getBoundingClientRect();
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
      area: Math.max(0, rect.width) * Math.max(0, rect.height)
    };
  };
  const finitePositive = (value, fallback = 0) => (
    Number.isFinite(value) && value > 0 ? value : fallback
  );
  const parseGridRows = element => {
    const value = getComputedStyle(element).gridTemplateRows || "";
    const rows = value
      .split(/\s+/)
      .map(token => Number.parseFloat(token))
      .filter(number => Number.isFinite(number) && number >= 0);
    return {
      computedValue: value,
      command: rows[0] || 0,
      workflow: rows[1] || 0
    };
  };
  const applySummaryGeometry = ({
    parentBlock,
    commandBlock,
    workflowBlock
  }) => {
    root.style.setProperty(
      "--flog-user-tab-summary-track-min-block",
      `${parentBlock}px`
    );
    root.style.setProperty(
      "--flog-user-command-summary-allocated-root-block",
      `${commandBlock}px`
    );
    root.style.setProperty(
      "--flog-user-workflow-summary-allocated-root-block",
      `${workflowBlock}px`
    );
    parent.style.setProperty(
      "grid-template-rows",
      `${commandBlock}px ${workflowBlock}px`,
      "important"
    );
    parent.style.setProperty("min-block-size", `${parentBlock}px`, "important");
    parent.style.setProperty("align-items", "stretch", "important");
    for (const [element, block, row] of [
      [command, commandBlock, "1"],
      [workflow, workflowBlock, "2"]
    ]) {
      element.style.setProperty("grid-row", row, "important");
      element.style.setProperty("order", "0", "important");
      element.style.setProperty("min-block-size", `${block}px`, "important");
      element.style.setProperty("block-size", `${block}px`, "important");
      element.style.setProperty("min-height", `${block}px`, "important");
      element.style.setProperty("height", `${block}px`, "important");
      element.style.setProperty("max-block-size", "none", "important");
      element.style.setProperty("max-height", "none", "important");
      element.style.setProperty("align-self", "stretch", "important");
    }
  };
  const geometryTargets = ({
    rootRect,
    parentRect,
    commandRect,
    workflowRect,
    gap,
    allocatedRootShares,
    trackSafetyRootShare
  }) => {
    const commandRequired = (
      allocatedRootShares.command * rootRect.area /
      Math.max(1, commandRect.width)
    );
    const workflowRequired = (
      allocatedRootShares.workflow * rootRect.area /
      Math.max(1, workflowRect.width)
    );
    const commandBlock = Math.max(
      commandRequired,
      command.scrollHeight || 0,
      commandRect.height
    );
    const workflowBlock = Math.max(
      workflowRequired,
      workflow.scrollHeight || 0,
      workflowRect.height
    );
    const safetyBlock = (
      trackSafetyRootShare * rootRect.area /
      Math.max(1, parentRect.width)
    );
    return {
      commandBlock,
      workflowBlock,
      safetyBlock,
      parentBlock: Math.max(
        1,
        commandBlock + workflowBlock + gap + safetyBlock
      )
    };
  };

  const rootRect0 = rectFacts(root);
  const parentRect0 = rectFacts(parent);
  const commandRect0 = rectFacts(command);
  const workflowRect0 = rectFacts(workflow);
  if (
    rootRect0.area <= 0 ||
    parentRect0.width <= 0 ||
    commandRect0.width <= 0 ||
    workflowRect0.width <= 0
  ) {
    return {
      required: true,
      state: "invalid-geometry",
      reason: "summary-slot calibration geometry has zero area or width"
    };
  }

  const requestedRootShares = {
    command: numberAttr(
      "data-flog-user-command-summary-required-root-share",
      0.026
    ),
    workflow: numberAttr(
      "data-flog-user-workflow-summary-required-root-share",
      0.055
    )
  };
  const allocatedRootShares = {
    command: numberAttr(
      "data-flog-user-command-summary-allocated-root-share",
      requestedRootShares.command
    ),
    workflow: numberAttr(
      "data-flog-user-workflow-summary-allocated-root-share",
      requestedRootShares.workflow
    )
  };
  const trackSafetyRootShare = numberAttr(
    "data-flog-user-summary-track-safety-root-share",
    0.018
  );
  const gap = Number.parseFloat(getComputedStyle(parent).rowGap || "0") || 0;

  // Pass one reserves the root track and directly sizes the nested summary
  // tracks.  The semantic summary nodes are themselves the painted slot owners.
  const targets0 = geometryTargets({
    rootRect: rootRect0,
    parentRect: parentRect0,
    commandRect: commandRect0,
    workflowRect: workflowRect0,
    gap,
    allocatedRootShares,
    trackSafetyRootShare
  });
  applySummaryGeometry(targets0);
  void root.offsetHeight;

  // Pass two recalculates against the realized parent and child widths.  Exact
  // pixel tracks avoid the previous mismatch where the parent received enough
  // space while an auto-sized summary node occupied only its content height.
  const rootRect1 = rectFacts(root);
  const parentRect1 = rectFacts(parent);
  const commandRect1 = rectFacts(command);
  const workflowRect1 = rectFacts(workflow);
  const parentRootShare1 = (
    parentRect1.area / Math.max(1, rootRect1.area)
  );
  const resolvedLocalShares = {
    command: Math.min(
      1,
      allocatedRootShares.command / Math.max(0.000001, parentRootShare1)
    ),
    workflow: Math.min(
      1,
      allocatedRootShares.workflow / Math.max(0.000001, parentRootShare1)
    )
  };
  resolvedLocalShares.safety = Math.max(
    0,
    1 - resolvedLocalShares.command - resolvedLocalShares.workflow
  );
  const targets1 = geometryTargets({
    rootRect: rootRect1,
    parentRect: parentRect1,
    commandRect: commandRect1,
    workflowRect: workflowRect1,
    gap,
    allocatedRootShares,
    trackSafetyRootShare
  });

  root.style.setProperty(
    "--flog-user-summary-parent-root-share",
    `${parentRootShare1 * 100}%`
  );
  root.style.setProperty(
    "--flog-user-command-summary-local-share",
    `${resolvedLocalShares.command * 100}%`
  );
  root.style.setProperty(
    "--flog-user-workflow-summary-local-share",
    `${resolvedLocalShares.workflow * 100}%`
  );
  root.style.setProperty(
    "--flog-user-summary-safety-local-share",
    `${resolvedLocalShares.safety * 100}%`
  );
  applySummaryGeometry(targets1);
  root.setAttribute(
    "data-flog-user-summary-parent-root-share",
    String(parentRootShare1)
  );
  root.setAttribute(
    "data-flog-user-workflow-summary-local-share",
    String(resolvedLocalShares.workflow)
  );
  root.setAttribute("data-flog-user-summary-calibrated", "true");
  root.setAttribute(
    "data-flog-user-summary-slot-mode",
    "semantic-node-fills-grid-track"
  );
  void root.offsetHeight;

  const rootRect2 = rectFacts(root);
  const parentRect2 = rectFacts(parent);
  const commandRect2 = rectFacts(command);
  const workflowRect2 = rectFacts(workflow);
  const finalParentRootShare = (
    parentRect2.area / Math.max(1, rootRect2.area)
  );
  const computedTracks = parseGridRows(parent);
  const commandSlotHeight = finitePositive(
    computedTracks.command,
    targets1.commandBlock
  );
  const workflowSlotHeight = finitePositive(
    computedTracks.workflow,
    targets1.workflowBlock
  );
  const summarySlotRootShares = {
    command: (
      parentRect2.width * commandSlotHeight /
      Math.max(1, rootRect2.area)
    ),
    workflow: (
      parentRect2.width * workflowSlotHeight /
      Math.max(1, rootRect2.area)
    )
  };
  const deliveredRootShares = {
    command: commandRect2.area / Math.max(1, rootRect2.area),
    workflow: workflowRect2.area / Math.max(1, rootRect2.area)
  };
  const summarySlotFillRatios = {
    command: Math.min(
      1,
      commandRect2.area /
      Math.max(1, parentRect2.width * commandSlotHeight)
    ),
    workflow: Math.min(
      1,
      workflowRect2.area /
      Math.max(1, parentRect2.width * workflowSlotHeight)
    )
  };
  const summarySlotAllocationPassed = (
    summarySlotRootShares.command + 0.0005 >= allocatedRootShares.command &&
    summarySlotRootShares.workflow + 0.0005 >= allocatedRootShares.workflow
  );
  const summarySlotFillPassed = (
    summarySlotFillRatios.command >= 0.98 &&
    summarySlotFillRatios.workflow >= 0.98
  );
  const semanticNodeDeliveryPassed = (
    deliveredRootShares.command + 0.0005 >= requestedRootShares.command &&
    deliveredRootShares.workflow + 0.0005 >= requestedRootShares.workflow
  );
  const passed = (
    summarySlotAllocationPassed &&
    summarySlotFillPassed &&
    semanticNodeDeliveryPassed
  );
  let state = "complete";
  if (!summarySlotAllocationPassed) {
    state = "slot-under-allocated";
  } else if (!summarySlotFillPassed) {
    state = "slot-not-filled";
  } else if (!semanticNodeDeliveryPassed) {
    state = "under-delivered";
  }

  return {
    required: true,
    state,
    calibrationMode: "realized-summary-slot-two-pass",
    ownershipMode: "semantic-node-fills-grid-track",
    requestedRootShares,
    allocatedRootShares,
    trackSafetyRootShare,
    gapPx: gap,
    initialParentRootShare: parentRect0.area / rootRect0.area,
    measuredParentRootShare: parentRootShare1,
    finalParentRootShare,
    parentWidthFraction: parentRect2.width / rootRect2.width,
    resolvedLocalShares,
    computedGridRows: computedTracks.computedValue,
    summarySlotRootShares,
    summarySlotFillRatios,
    summarySlotAllocationPassed,
    summarySlotFillPassed,
    semanticNodeDeliveryPassed,
    appliedPixelBlocks: {
      command: targets1.commandBlock,
      workflow: targets1.workflowBlock,
      safety: targets1.safetyBlock,
      parent: targets1.parentBlock
    },
    deliveredRootShares,
    passed
  };
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
    devicePixelRatio: Number(win.devicePixelRatio || 1) || 1,
  };
  const devicePixelRatio = viewport.devicePixelRatio;

  function pixelRectFromCssRect(rect) {
    const leftCss = Number(rect.left ?? rect.x ?? 0);
    const topCss = Number(rect.top ?? rect.y ?? 0);
    const rightCss = Number(rect.right ?? (leftCss + Number(rect.width || 0)));
    const bottomCss = Number(rect.bottom ?? (topCss + Number(rect.height || 0)));
    const left = Math.round(leftCss * devicePixelRatio);
    const top = Math.round(topCss * devicePixelRatio);
    const right = Math.round(rightCss * devicePixelRatio);
    const bottom = Math.round(bottomCss * devicePixelRatio);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return {x: left, y: top, left, top, right, bottom, width, height, area: width * height};
  }

  function cssRectFromPixelRect(rect) {
    const left = Number(rect.left || 0) / devicePixelRatio;
    const top = Number(rect.top || 0) / devicePixelRatio;
    const right = Number(rect.right || 0) / devicePixelRatio;
    const bottom = Number(rect.bottom || 0) / devicePixelRatio;
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return {x: left, y: top, left, top, right, bottom, width, height, area: width * height};
  }

  function withPixelRect(rect) {
    return {...rect, pixelRect: pixelRectFromCssRect(rect)};
  }

  function documentRectFromViewportRect(rect) {
    const documentRect = {
      x: rect.left + win.scrollX,
      y: rect.top + win.scrollY,
      left: rect.left + win.scrollX,
      top: rect.top + win.scrollY,
      right: rect.right + win.scrollX,
      bottom: rect.bottom + win.scrollY,
      width: rect.width,
      height: rect.height,
      area: rect.area,
    };
    return withPixelRect(documentRect);
  }

  function rectObj(rect) {
    const left = Number(rect.left);
    const top = Number(rect.top);
    const width = Math.max(0, Number(rect.width));
    const height = Math.max(0, Number(rect.height));
    return withPixelRect({x: left, y: top, left, top, right: left + width, bottom: top + height, width, height, area: width * height});
  }

  function intersect(a, b) {
    const left = Math.max(a.left, b.left);
    const top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right);
    const bottom = Math.min(a.bottom, b.bottom);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return withPixelRect({x: left, y: top, left, top, right, bottom, width, height, area: width * height});
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
      documentRect: documentRectFromViewportRect(raw),
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
      documentRect: documentRectFromViewportRect(raw),
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
      mode: "exclusive-enforced",
      enforced: true,
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
      pixelGeometry: "css-edge-rounded-device-pixels-v1",
      devicePixelRatio,
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
  const rootDocumentRect = documentRectFromViewportRect(rootRaw);

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
  const paintedOwnershipShadow = paintedOwnershipShadowFor(
    nodeElements,
    nodeRecords,
    unitElements,
    unitRecords,
    rootClipped,
    root.getAttribute("data-flog-focus-slot") || ""
  );
  const ownershipBySlot = new Map(
    (paintedOwnershipShadow.nodes || []).map((item) => [item.slot, item])
  );
  const ownershipByUnit = new Map(
    (paintedOwnershipShadow.units || []).map((item) => [item.unitId, item])
  );
  for (const record of nodeRecords) {
    const ownership = ownershipBySlot.get(record.slot);
    if (!ownership) continue;
    record.rawVisibleArea = ownership.rawVisibleArea;
    record.rawVisibleShare = ownership.rawVisibleShare;
    record.effectiveVisibleArea = ownership.effectiveVisibleArea;
    record.effectiveVisibleShare = ownership.effectiveVisibleShare;
    record.occludedArea = ownership.occludedArea;
    record.occludedShare = ownership.occludedShare;
    record.occludedBy = ownership.occludedBy;
  }
  for (const record of unitRecords) {
    const ownership = ownershipByUnit.get(record.unitId);
    if (!ownership) continue;
    record.rawVisibleArea = ownership.rawVisibleArea;
    record.rawVisibleShare = ownership.rawVisibleShare;
    record.effectiveOwnedArea = ownership.effectiveOwnedArea;
    record.effectiveOwnedShare = ownership.effectiveOwnedShare;
  }
  if (focusRecord) {
    const ownership = ownershipBySlot.get(focusRecord.slot);
    if (ownership) Object.assign(focusRecord, ownership);
  }

  let visibleRequired = requiredNodes
    .map(recordFor)
    .filter((item) => {
      const ownership = ownershipBySlot.get(item.slot);
      const area = ownership?.effectiveVisibleArea ?? intersect(item.rect, rootClipped).area;
      return item.rect.area > 4 && area > 4;
    });
  const nodeArea = paintedOwnershipShadow.exclusiveOwnedArea;
  const nodeCoverageRatio =
    rootClipped.area > 0 ? nodeArea / rootClipped.area : 0;
  const rootUnclaimedAreaRatio =
    rootClipped.area > 0 ? Math.max(0, 1 - nodeCoverageRatio) : 1;

  const focusUnitId = focusRecord?.unitId || "";
  const focusUnitRecord = focusUnitId
    ? unitRecords.find((item) => item.unitId === focusUnitId) || null
    : null;
  const focusUnitOwnership = focusUnitId
    ? ownershipByUnit.get(focusUnitId) || null
    : null;
  const activeSemanticRecords = nodeRecords.filter(
    (item) =>
      item.realization !== "compact-trigger" &&
      item.realization !== "absent" &&
      (item.effectiveVisibleArea || 0) > 4
  );
  let activePresentationMode = "semantic-envelope";
  let activePresentationUnitId = "";
  let activePresentationBounds = intersect(
    unionBounds(activeSemanticRecords.map((item) => item.rect)),
    rootClipped
  );
  let activePresentationOwnedArea = Math.min(
    activePresentationBounds.area,
    activeSemanticRecords.reduce(
      (total, item) => total + Math.max(0, item.effectiveVisibleArea || 0),
      0
    )
  );
  if (
    focusUnitRecord &&
    focusUnitOwnership &&
    focusUnitRecord.rect.area > 4
  ) {
    activePresentationMode = "focus-layout-unit";
    activePresentationUnitId = focusUnitId;
    activePresentationBounds = intersect(focusUnitRecord.rect, rootClipped);
    activePresentationOwnedArea = Math.min(
      activePresentationBounds.area,
      Math.max(0, focusUnitOwnership.effectiveOwnedArea || 0)
    );
  }
  if (activePresentationBounds.area <= 4) {
    activePresentationMode = "root-fallback";
    activePresentationUnitId = "";
    activePresentationBounds = rootClipped;
    activePresentationOwnedArea = Math.min(rootClipped.area, nodeArea);
  }
  const activePresentationOccupancy =
    activePresentationBounds.area > 0
      ? Math.max(
          0,
          Math.min(1, activePresentationOwnedArea / activePresentationBounds.area)
        )
      : 0;
  const activePresentationUnclaimedRatio =
    Math.max(0, 1 - activePresentationOccupancy);
  const intentionalInactiveRootRatio =
    rootClipped.area > 0
      ? Math.max(
          0,
          1 - Math.min(1, activePresentationBounds.area / rootClipped.area)
        )
      : 0;
  const accidentalUnclaimedRootRatio =
    rootClipped.area > 0
      ? Math.max(
          0,
          (activePresentationBounds.area - activePresentationOwnedArea) /
            rootClipped.area
        )
      : 1;
  // Stage C makes phase-relative active density authoritative while preserving
  // root-wide unclaimed space as a separate diagnostic.
  const unclaimedAreaRatio = activePresentationUnclaimedRatio;
  const focusClipped = focusRecord ? intersect(focusRecord.rect, rootClipped) : {left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, area: 0};
  const rawFocusVisibleArea = focusClipped.area;
  const rawFocusShare =
    rootClipped.area > 0 ? rawFocusVisibleArea / rootClipped.area : 0;
  const effectiveFocusVisibleArea =
    paintedOwnershipShadow.effectiveFocusShare * rootClipped.area;
  const focusVisibleArea = effectiveFocusVisibleArea;
  const focusShare = paintedOwnershipShadow.effectiveFocusShare;
  const desiredFocusShare = Number(root.getAttribute("data-flog-desired-focus-share") || "0.45");
  const minFocusShare = Number(root.getAttribute("data-flog-min-focus-share") || "0.3");
  const phaseFloorTolerance = Number(
    root.getAttribute("data-flog-phase-floor-tolerance") || "0.0005"
  );
  const maxFocusShare = Number(root.getAttribute("data-flog-max-focus-share") || "0.75");
  const focusDeviation = Math.abs(focusShare - desiredFocusShare);
  const phaseName = phase || root.getAttribute("data-flog-phase") || "default";
  const focusSlotName = root.getAttribute("data-flog-focus-slot") || "focus";
  function shareFloorGate(actual, floor) {
    const rawHeadroom = actual - floor;
    const met = rawHeadroom >= -phaseFloorTolerance;
    return {
      met,
      actual,
      floor,
      tolerance: phaseFloorTolerance,
      rawHeadroom,
      headroom: met ? Math.max(0, rawHeadroom) : rawHeadroom,
      shortfall: Math.max(0, floor - actual),
    };
  }
  function sharePercent(value) {
    return `${(value * 100).toFixed(2)}%`;
  }
  function shareFloorFailureReason(phaseValue, slotValue, gate) {
    return `${phaseValue} phase gives ${slotValue} ${sharePercent(gate.actual)} ` +
      `against phase floor ${sharePercent(gate.floor)} ` +
      `(shortfall ${sharePercent(gate.shortfall)}; tolerance ${sharePercent(gate.tolerance)})`;
  }
  const focusFloorGate = shareFloorGate(focusShare, minFocusShare);
  const focusMeetsMinimum = focusFloorGate.met;
  const focusFloorFailureReason = shareFloorFailureReason(
    phaseName,
    focusSlotName,
    focusFloorGate
  );

  const focusContentRecords = focus
    ? Array.from(focus.querySelectorAll("[data-flog-item='true']"))
        .filter((el) => isVisibleByStyle(el))
        .map(recordFor)
        .filter((item) => item.rect.area > 4 && intersect(item.rect, focusClipped).area > 4)
    : [];
  const rawFocusContentArea = unionArea(
    focusContentRecords.map((item) => item.rect),
    focusClipped
  );
  const focusOccludedArea = Math.max(
    0,
    rawFocusVisibleArea - effectiveFocusVisibleArea
  );
  const focusContentArea = Math.max(
    0,
    rawFocusContentArea - focusOccludedArea
  );
  const usefulFocusOccupancy =
    effectiveFocusVisibleArea > 0
      ? Math.min(1, focusContentArea / effectiveFocusVisibleArea)
      : 0;
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
    if (Number.isFinite(record.effectiveVisibleShare)) {
      return Math.max(0, Math.min(1, record.effectiveVisibleShare));
    }
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
  const blockedCriticalControls = criticalControlInterceptionShadow.filter(
    (item) =>
      item.fullyForeignIntercepted ||
      (
        item.foreignInterceptedPointCount > 0 &&
        item.actionableShare < 0.60
      )
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
  const partitionOverlapTolerance = 0.002;
  const undeclaredPartitionOverlapShare = Math.max(
    paintedOwnershipShadow.undeclaredPartitionOverlapShare || 0,
    paintedOwnershipShadow.partitionOverlapCellShare || 0
  );
  const partitionOwnershipViolation =
    undeclaredPartitionOverlapShare > partitionOverlapTolerance;
  const overlayBudgetViolations =
    paintedOwnershipShadow.overlayBudgetExceeded || [];

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
  if (unclaimedAreaRatio > 0.34) warnings.push(`high active-presentation unclaimed area (${unclaimedAreaRatio.toFixed(2)})`);
  if (!focusMeetsMinimum) warnings.push(focusFloorFailureReason);
  if (focusShare > maxFocusShare) warnings.push(`focus slot dominates beyond target (${focusShare.toFixed(2)} > ${maxFocusShare.toFixed(2)})`);
  if (usefulFocusOccupancy < minUsefulFocusOccupancy) warnings.push(`focus interior is too sparse (${usefulFocusOccupancy.toFixed(2)} < ${minUsefulFocusOccupancy.toFixed(2)})`);
  if (focusContentRecords.length < 4) warnings.push(`focus slot has too few useful interior elements (${focusContentRecords.length})`);
  if (clippedCriticalControls.length) warnings.push(`${clippedCriticalControls.length} critical control(s) extend outside viewport/root`);
  if (hiddenCriticalControls.length) warnings.push(`${hiddenCriticalControls.length} critical control(s) are hidden or zero-sized`);
  if (blockedCriticalControls.length) warnings.push(`${blockedCriticalControls.length} active critical control(s) are foreign-intercepted`);
  if (partitionOwnershipViolation) warnings.push(`partition ownership overlap exceeds tolerance (${undeclaredPartitionOverlapShare.toFixed(3)} > ${partitionOverlapTolerance.toFixed(3)})`);
  if (overlayBudgetViolations.length) warnings.push(`${overlayBudgetViolations.length} declared overlay budget(s) were exceeded`);
  if (visibleRequired.length < requiredNodes.length) warnings.push("some required primary/focus nodes are not effectively visible");
  if (missingRequiredCompanions.length) warnings.push(`required companion slot(s) not visible enough: ${missingRequiredCompanions.map((item) => item.slot).join(", ")}`);
  if (hiddenForbiddenSlots.length) warnings.push(`forbidden default-hidden slot(s) are missing from the default view: ${hiddenForbiddenSlots.map((item) => item.slot).join(", ")}`);
  if (distantNearbyCompanions.length) warnings.push(`nearby companion slot(s) are too far from focus: ${distantNearbyCompanions.map((item) => item.slot).join(", ")}`);
  const sourceOrderStarvesHighFocus = candidateFamily === "source-order-stacked" && minFocusShare >= 0.40 && !focusMeetsMinimum;
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
  if (focusMeetsMinimum && focusShare <= maxFocusShare) {
    positiveReasons.push(`focus ${root.getAttribute("data-flog-focus-slot") || "slot"} got ${pct(focusShare)} of the root against target ${pct(desiredFocusShare)}`);
  } else if (!focusMeetsMinimum) {
    failureReasons.push(focusFloorFailureReason);
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
    positiveReasons.push(`active-presentation unclaimed area is controlled at ${pct(unclaimedAreaRatio)}`);
  } else {
    failureReasons.push(`active-presentation unclaimed area is high at ${pct(unclaimedAreaRatio)}`);
  }
  if (
    clippedCriticalControls.length === 0 &&
    hiddenCriticalControls.length === 0 &&
    blockedCriticalControls.length === 0
  ) {
    positiveReasons.push("no active critical controls were clipped, hidden, or foreign-intercepted");
  } else {
    failureReasons.push(
      `${clippedCriticalControls.length + hiddenCriticalControls.length} critical control(s) were clipped or hidden; ` +
      `${blockedCriticalControls.length} were foreign-intercepted`
    );
  }
  if (!partitionOwnershipViolation) {
    positiveReasons.push(
      `partition overlap stayed within ${(partitionOverlapTolerance * 100).toFixed(1)}% tolerance`
    );
  } else {
    failureReasons.push(
      `undeclared partition overlap is ${(undeclaredPartitionOverlapShare * 100).toFixed(1)}% of the root`
    );
  }
  if (!overlayBudgetViolations.length) {
    positiveReasons.push("declared overlays stayed inside their occlusion budgets");
  } else {
    failureReasons.push(
      `${overlayBudgetViolations.length} declared overlay budget(s) were exceeded`
    );
  }
  const hardGeometryGatePassed =
    focusMeetsMinimum &&
    companionVisibilityRatio === 1 &&
    clippedCriticalControls.length === 0 &&
    hiddenCriticalControls.length === 0 &&
    blockedCriticalControls.length === 0 &&
    !partitionOwnershipViolation &&
    overlayBudgetViolations.length === 0 &&
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
  reviewNotes.push("Stage B enforces exclusive painted ownership, overlay budgets, partition isolation, and foreign critical-control interception.");
  reviewNotes.push(
    `Stage E applies one absolute effective-share floor gate with a ${(phaseFloorTolerance * 100).toFixed(2)}% subpixel tolerance in browser and aggregate scoring.`
  );
  reviewNotes.push(
    `Stage C scores density inside the ${activePresentationMode}` +
    `${activePresentationUnitId ? ` (${activePresentationUnitId})` : ""}; ` +
    `${pct(intentionalInactiveRootRatio)} of root space remains intentionally outside that active envelope.`
  );
  reviewNotes.push("Human review still must compare the PNG proof against the intended app meaning.");

  let score = 100;
  score -= Math.round(unclaimedAreaRatio * 54);
  score -= Math.round(focusDeviation * 72);
  if (!focusMeetsMinimum) score -= 16;
  if (focusShare > maxFocusShare) score -= 8;
  score -= Math.round(Math.max(0, minUsefulFocusOccupancy - usefulFocusOccupancy) * 72);
  score -= Math.min(14, Math.max(0, 4 - focusContentRecords.length) * 4);
  score -= clippedCriticalControls.length * 8;
  score -= hiddenCriticalControls.length * 10;
  score -= blockedCriticalControls.length * 12;
  if (partitionOwnershipViolation) {
    score -= Math.min(
      28,
      12 + Math.round(
        (undeclaredPartitionOverlapShare - partitionOverlapTolerance) * 120
      )
    );
  }
  score -= overlayBudgetViolations.length * 16;
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
  const status = !hardGeometryGatePassed
    ? "fail"
    : score >= 82 && warnings.length <= 1
      ? "pass"
      : score >= 64
        ? "watch"
        : "fail";

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
    const rect = record.documentRect || documentRectFromViewportRect(record);
    const pixelAlignedRect = rect.pixelRect ? cssRectFromPixelRect(rect.pixelRect) : rect;
    if (!pixelAlignedRect || pixelAlignedRect.width <= 1 || pixelAlignedRect.height <= 1) return;
    const box = doc.createElement("div");
    box.setAttribute("data-flog-snapshot-overlay", "box");
    box.style.position = "absolute";
    box.style.left = `${pixelAlignedRect.left}px`;
    box.style.top = `${pixelAlignedRect.top}px`;
    box.style.width = `${pixelAlignedRect.width}px`;
    box.style.height = `${pixelAlignedRect.height}px`;
    box.style.border = `1px solid ${color}`;
    box.style.background = fill;
    box.style.boxSizing = "border-box";
    box.style.borderRadius = "0";
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
        `ENFORCED overlay ${item.slot} → ${item.overlayTarget || "declared target"}`,
        "repeating-linear-gradient(135deg, rgba(255,159,67,.16) 0 6px, rgba(255,159,67,.03) 6px 12px)"
      );
    });
  scrollOwners.slice(0, 14).forEach((item) => {
    const rect = item.rect;
    addBox(
      {documentRect: documentRectFromViewportRect(rect)},
      "#ffbf3f",
      `scroll ${item.slot || item.selector}`,
      "rgba(255,191,63,0.06)"
    );
  });
  clippedCriticalControls.slice(0, 18).forEach((item) => addBox(item, "#ff4d4d", `clipped ${item.selector}`, "rgba(255,77,77,0.06)"));
  hiddenCriticalControls.slice(0, 12).forEach((item, index) => {
    const placeholderRect = {
      left: rootDocumentRect.left + 8,
      top: rootDocumentRect.top + 28 + index * 18,
      right: rootDocumentRect.left + 228,
      bottom: rootDocumentRect.top + 44 + index * 18,
      width: 220,
      height: 16,
      area: 3520,
    };
    addBox(
      {documentRect: withPixelRect(placeholderRect)},
      "#ff4d4d",
      `hidden ${item.selector}`,
      "rgba(255,77,77,0.16)"
    );
  });
  partiallyInterceptedCriticalControlsShadow.slice(0, 18).forEach((item) => {
    const label = item.fullyForeignIntercepted
      ? "foreign intercepted"
      : "partially foreign intercepted";
    addBox(
      item,
      "#ff3df2",
      `ENFORCED ${label} ${item.selector}`,
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
    `score=${score} status=${status} active-unclaimed=${unclaimedAreaRatio.toFixed(3)} root-unclaimed=${rootUnclaimedAreaRatio.toFixed(3)} intentional-inactive-root=${intentionalInactiveRootRatio.toFixed(3)} focus(raw)=${rawFocusShare.toFixed(3)} focus(effective)=${focusShare.toFixed(3)} floor=${minFocusShare.toFixed(3)} floor-met=${focusFloorGate.met} target=${desiredFocusShare.toFixed(2)} focus-occupancy=${usefulFocusOccupancy.toFixed(3)} companion-proximity=${companionProximityScore.toFixed(3)}`,
    `painted-ownership=exclusive-enforced exclusive=${paintedOwnershipShadow.exclusiveOwnedShare.toFixed(3)} double-claimed=${paintedOwnershipShadow.doubleClaimedShare.toFixed(3)} overlay=${paintedOwnershipShadow.declaredOverlayShare.toFixed(3)} undeclared-partition-overlap=${undeclaredPartitionOverlapShare.toFixed(3)} blocked-controls=${blockedCriticalControls.length}`,
    `<span style="color:#43a7ff">blue=root</span> <span style="color:#00c2c7">cyan=layout unit</span> <span style="color:#38d66b">green=semantic node</span> <span style="color:#b46cff">purple=focus target</span> <span style="color:#ffbf3f">orange=scroll</span> <span style="color:#ff9f43">hatched=declared overlay (enforced)</span> <span style="color:#ff3df2">magenta=foreign interception (enforced)</span> <span style="color:#ff4d4d">red=clipped/hidden</span>`,
  ].join("<br>");
  overlay.appendChild(legend);

  return {
    hierarchyId,
    candidate,
    pixelGeometry: "css-edge-rounded-device-pixels-v1",
    devicePixelRatio,
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
      rootUnclaimedAreaRatio,
      unclaimedAreaRatio,
      activePresentationMode,
      activePresentationUnitId,
      activePresentationEnvelope: activePresentationBounds,
      activePresentationOwnedArea,
      activePresentationOccupancy,
      activePresentationUnclaimedRatio,
      intentionalInactiveRootRatio,
      accidentalUnclaimedRootRatio,
      rawFocusShare,
      focusShare,
      effectiveFocusShare: focusShare,
      focusOccludedShare: paintedOwnershipShadow.focusOccludedShare,
      paintedOwnershipMode: "exclusive-enforced",
      paintedOwnershipEnforced: true,
      paintedOwnership: paintedOwnershipShadow,
      paintedOwnershipShadow,
      effectiveFocusShareShadow: focusShare,
      focusOccludedShareShadow: paintedOwnershipShadow.focusOccludedShare,
      exclusiveOwnedShare: paintedOwnershipShadow.exclusiveOwnedShare,
      exclusiveOwnedShareShadow: paintedOwnershipShadow.exclusiveOwnedShare,
      doubleClaimedShare: paintedOwnershipShadow.doubleClaimedShare,
      doubleClaimedShareShadow: paintedOwnershipShadow.doubleClaimedShare,
      declaredOverlayShare: paintedOwnershipShadow.declaredOverlayShare,
      declaredOverlayShareShadow: paintedOwnershipShadow.declaredOverlayShare,
      undeclaredPartitionOverlapShare,
      undeclaredPartitionOverlapShareShadow:
        undeclaredPartitionOverlapShare,
      partitionOverlapCellShare:
        paintedOwnershipShadow.partitionOverlapCellShare,
      partitionOverlapCellShareShadow:
        paintedOwnershipShadow.partitionOverlapCellShare,
      blockedCriticalControlCount: blockedCriticalControls.length,
      interceptedCriticalControlCount:
        fullyInterceptedCriticalControlsShadow.length,
      interceptedCriticalControlCountShadow:
        fullyInterceptedCriticalControlsShadow.length,
      partiallyInterceptedCriticalControlCount:
        partiallyInterceptedCriticalControlsShadow.length,
      partiallyInterceptedCriticalControlCountShadow:
        partiallyInterceptedCriticalControlsShadow.length,
      foreignInterceptedCriticalControlCount:
        partiallyInterceptedCriticalControlsShadow.length,
      foreignInterceptedCriticalControlCountShadow:
        partiallyInterceptedCriticalControlsShadow.length,
      controlInterceptionOutcomeTotals: controlInterceptionOutcomeTotalsShadow,
      controlInterceptionOutcomeTotalsShadow,
      desiredFocusShare,
      minFocusShare,
      phaseFloorTolerance,
      focusFloorMet: focusFloorGate.met,
      focusFloorShortfall: focusFloorGate.shortfall,
      focusRawHeadroom: focusFloorGate.rawHeadroom,
      focusHeadroom: focusFloorGate.headroom,
      maxFocusShare,
      focusDeviation,
      focusContentCount: focusContentRecords.length,
      rawFocusContentArea,
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
      criticalControlInterception:
        criticalControlInterceptionShadow.slice(0, 40),
      criticalControlInterceptionShadow:
        criticalControlInterceptionShadow.slice(0, 40),
      blockedCriticalControls:
        blockedCriticalControls.slice(0, 20),
      fullyInterceptedCriticalControls:
        fullyInterceptedCriticalControlsShadow.slice(0, 20),
      fullyInterceptedCriticalControlsShadow:
        fullyInterceptedCriticalControlsShadow.slice(0, 20),
      partiallyInterceptedCriticalControls:
        partiallyInterceptedCriticalControlsShadow.slice(0, 20),
      partiallyInterceptedCriticalControlsShadow:
        partiallyInterceptedCriticalControlsShadow.slice(0, 20),
      paintedOwnershipOverlapMatrix:
        paintedOwnershipShadow.overlapMatrix.slice(0, 80),
      undeclaredPartitionOverlapMatrix:
        paintedOwnershipShadow.undeclaredPartitionOverlapMatrix.slice(0, 40),
      undeclaredPartitionOverlapMatrixShadow:
        paintedOwnershipShadow.undeclaredPartitionOverlapMatrix.slice(0, 40),
      controlInterceptionOutcomeTotals: controlInterceptionOutcomeTotalsShadow,
      controlInterceptionOutcomeTotalsShadow,
      scrollOwners: scrollOwners.slice(0, 20),
    },
    classification: {
      score,
      status,
      warnings,
      positiveReasons,
      failureReasons,
      reviewNotes,
      phaseFloorGate: focusFloorGate,
    },
    humanLoop: {
      required: true,
      proved: [
        "Chromium rendered the candidate layout.",
        "The PNG shows the root, semantic nodes, focus target, scroll owners, and clipped/hidden controls.",
        "The report measured active-presentation density separately from root-wide inactive space.",
        "The report measured how much of the root was given to the declared focus slot.",
        "Exclusive painted ownership, inter-unit partition isolation, declared overlay budgets, and critical-control interception were enforced during ranking.",
      ],
      inferred: [
        "Whether space outside the active presentation envelope is desirable calm spacing for the real app.",
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



RENDERED_POLICY_FINGERPRINT_VERSION = "cross-phase-painted-device-pixel-geometry-v2"
RENDERED_POLICY_FINGERPRINT_BINS = 500
PIXEL_RECT_AUDIT_VERSION = "authoritative-pixel-rect-audit-v1"
AUTHORITATIVE_PIXEL_COORDINATE_SYSTEM = "device-pixel-rect"


def _normalized_rect(
    rect: dict[str, Any],
    *,
    coordinate_system: str,
    source: str,
) -> dict[str, float | str] | None:
    if not isinstance(rect, dict):
        return None
    left = float(rect.get("left", rect.get("x", 0)) or 0)
    top = float(rect.get("top", rect.get("y", 0)) or 0)
    width = float(rect.get("width", 0) or 0)
    height = float(rect.get("height", 0) or 0)
    right = float(rect.get("right", left + width) or 0)
    bottom = float(rect.get("bottom", top + height) or 0)
    if width <= 0 and right >= left:
        width = right - left
    if height <= 0 and bottom >= top:
        height = bottom - top
    if width <= 0 or height <= 0:
        return None
    return {
        "x": left,
        "y": top,
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": width,
        "height": height,
        "area": width * height,
        "coordinateSystem": coordinate_system,
        "source": source,
    }


def authoritative_pixel_rect(
    rect: dict[str, Any] | None,
    *,
    allow_css_fallback: bool = True,
) -> dict[str, float | str] | None:
    """Return the one rect FLOG is allowed to use for rendered proof geometry.

    Browser measurements carry fractional CSS rectangles and an edge-rounded
    ``pixelRect`` generated from those CSS edges.  Fingerprints, rollups, and
    audits must prefer ``pixelRect`` so they stay on the same integer device
    pixel grid as screenshots and overlays.
    """

    if not isinstance(rect, dict):
        return None
    pixel_rect = rect.get("pixelRect")
    if isinstance(pixel_rect, dict):
        normalized = _normalized_rect(
            pixel_rect,
            coordinate_system=AUTHORITATIVE_PIXEL_COORDINATE_SYSTEM,
            source="pixelRect",
        )
        if normalized is not None:
            return normalized
    if not allow_css_fallback:
        return None
    return _normalized_rect(
        rect,
        coordinate_system="css-fallback",
        source="cssRect",
    )


def _record_authoritative_rect(record: dict[str, Any]) -> dict[str, float | str] | None:
    if not isinstance(record, dict):
        return None
    rect = record.get("rect") or record.get("documentRect")
    if isinstance(rect, dict):
        return authoritative_pixel_rect(rect)
    return authoritative_pixel_rect(record)


def _fingerprint_root_rect(phase_measurement: dict[str, Any]) -> dict[str, float | str] | None:
    root = ((phase_measurement.get("geometryFacts") or {}).get("root") or {})
    for key in ("clipped", "documentRect", "raw"):
        rect = authoritative_pixel_rect(root.get(key))
        if rect is not None:
            return rect
    return None


def _quantized_relative_rect(
    record: dict[str, Any],
    root_rect: dict[str, float] | None,
    *,
    bins: int = RENDERED_POLICY_FINGERPRINT_BINS,
) -> tuple[int, int, int, int] | None:
    rect = _record_authoritative_rect(record)
    if not isinstance(rect, dict) or not root_rect:
        return None
    root_width = max(1.0, float(root_rect.get("width", 0) or 0))
    root_height = max(1.0, float(root_rect.get("height", 0) or 0))
    return (
        round(
            (
                float(rect.get("left", rect.get("x", 0)) or 0)
                - float(root_rect.get("left", root_rect.get("x", 0)) or 0)
            )
            / root_width
            * bins
        ),
        round(
            (
                float(rect.get("top", rect.get("y", 0)) or 0)
                - float(root_rect.get("top", root_rect.get("y", 0)) or 0)
            )
            / root_height
            * bins
        ),
        round(float(rect.get("width", 0) or 0) / root_width * bins),
        round(float(rect.get("height", 0) or 0) / root_height * bins),
    )


def rendered_policy_fingerprint_payload(
    item: dict[str, Any],
    *,
    bins: int = RENDERED_POLICY_FINGERPRINT_BINS,
) -> dict[str, Any] | None:
    """Describe cross-phase rendered geometry without including policy names.

    Policy metadata is intentionally excluded.  Two differently named local
    policies therefore collide when they produce the same slot/unit ownership
    and quantized browser rectangles across every realized phase.
    """

    phase_measurements = list(item.get("phaseMeasurements") or [item])
    payload_phases: list[dict[str, Any]] = []
    geometry_seen = False

    for phase_measurement in sorted(
        phase_measurements,
        key=lambda value: str(value.get("phase") or ""),
    ):
        root_rect = _fingerprint_root_rect(phase_measurement)
        examples = phase_measurement.get("examples") or {}
        node_rows: list[tuple[Any, ...]] = []
        for record in examples.get("nodes") or []:
            relative_rect = _quantized_relative_rect(record, root_rect, bins=bins)
            if relative_rect is not None:
                geometry_seen = True
            node_rows.append(
                (
                    str(record.get("slot") or ""),
                    str(record.get("realization") or ""),
                    str(record.get("ownershipMode") or ""),
                    str(record.get("unitId") or ""),
                    str(record.get("unitRole") or ""),
                    relative_rect,
                    round(float(record.get("effectiveVisibleShare", 0) or 0) * bins),
                )
            )

        unit_rows: list[tuple[Any, ...]] = []
        for record in examples.get("units") or []:
            relative_rect = _quantized_relative_rect(record, root_rect, bins=bins)
            if relative_rect is not None:
                geometry_seen = True
            unit_rows.append(
                (
                    str(record.get("unitId") or ""),
                    str(record.get("role") or ""),
                    str(record.get("realization") or ""),
                    str(record.get("ownershipMode") or ""),
                    tuple(sorted(str(value) for value in (record.get("activeSlots") or []))),
                    tuple(
                        sorted(
                            str(value)
                            for value in (record.get("activeSupportSlots") or [])
                        )
                    ),
                    tuple(
                        sorted(str(value) for value in (record.get("triggerSlots") or []))
                    ),
                    relative_rect,
                )
            )

        payload_phases.append(
            {
                "phase": str(phase_measurement.get("phase") or ""),
                "focusSlot": str(phase_measurement.get("focusSlot") or ""),
                "realizationStates": sorted(
                    (
                        str(slot),
                        str(state),
                    )
                    for slot, state in (
                        phase_measurement.get("realizationStates") or {}
                    ).items()
                ),
                "nodes": sorted(node_rows, key=repr),
                "units": sorted(unit_rows, key=repr),
            }
        )

    if not geometry_seen:
        return None
    return {
        "version": RENDERED_POLICY_FINGERPRINT_VERSION,
        "bins": int(bins),
        "coordinateSystem": AUTHORITATIVE_PIXEL_COORDINATE_SYSTEM,
        "phases": payload_phases,
    }


def _iter_pixel_audit_records(measurement: dict[str, Any]):
    phase_measurements = list(measurement.get("phaseMeasurements") or [measurement])
    for phase_index, phase_measurement in enumerate(phase_measurements):
        root = ((phase_measurement.get("geometryFacts") or {}).get("root") or {})
        for key in ("clipped", "documentRect", "raw"):
            rect = root.get(key)
            if isinstance(rect, dict):
                yield f"phase[{phase_index}].geometryFacts.root.{key}", rect
        examples = phase_measurement.get("examples") or {}
        for section in ("nodes", "units", "focusContent", "clippedCriticalControls", "scrollOwners"):
            records = examples.get(section) or []
            if isinstance(records, dict):
                records = [records]
            for record_index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                for key in ("rect", "documentRect"):
                    rect = record.get(key)
                    if isinstance(rect, dict):
                        yield f"phase[{phase_index}].examples.{section}[{record_index}].{key}", rect
        focus = examples.get("focus")
        if isinstance(focus, dict):
            for key in ("rect", "documentRect"):
                rect = focus.get(key)
                if isinstance(rect, dict):
                    yield f"phase[{phase_index}].examples.focus.{key}", rect


def audit_authoritative_pixel_rect_coordinate_system(
    measurement: dict[str, Any],
) -> dict[str, Any]:
    pixel_rect_records = 0
    css_fallback_records = 0
    missing_records: list[str] = []
    for path, rect in _iter_pixel_audit_records(measurement):
        chosen = authoritative_pixel_rect(rect, allow_css_fallback=True)
        if chosen is None:
            missing_records.append(path)
        elif chosen.get("source") == "pixelRect":
            pixel_rect_records += 1
        else:
            css_fallback_records += 1
    pixel_geometry = str(measurement.get("pixelGeometry") or "")
    browser_pixel_geometry = pixel_geometry == "css-edge-rounded-device-pixels-v1"
    return {
        "version": PIXEL_RECT_AUDIT_VERSION,
        "coordinateSystem": AUTHORITATIVE_PIXEL_COORDINATE_SYSTEM,
        "pixelGeometry": pixel_geometry,
        "browserPixelGeometry": browser_pixel_geometry,
        "ok": bool(pixel_rect_records and not missing_records and (not browser_pixel_geometry or css_fallback_records == 0)),
        "pixelRectRecordCount": pixel_rect_records,
        "cssFallbackRecordCount": css_fallback_records,
        "missingRecordCount": len(missing_records),
        "missingRecordExamples": missing_records[:12],
        "verdict": (
            "authoritative-pixelRect"
            if pixel_rect_records and not css_fallback_records and not missing_records
            else "css-fallback-present"
            if css_fallback_records
            else "missing-rendered-geometry"
        ),
    }


def pixel_rect_audit_summary(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [audit_authoritative_pixel_rect_coordinate_system(item) for item in measurements]
    return {
        "version": PIXEL_RECT_AUDIT_VERSION,
        "coordinateSystem": AUTHORITATIVE_PIXEL_COORDINATE_SYSTEM,
        "measurementCount": len(audits),
        "okCount": sum(1 for item in audits if item.get("ok")),
        "cssFallbackRecordCount": sum(int(item.get("cssFallbackRecordCount", 0) or 0) for item in audits),
        "missingRecordCount": sum(int(item.get("missingRecordCount", 0) or 0) for item in audits),
        "pixelRectRecordCount": sum(int(item.get("pixelRectRecordCount", 0) or 0) for item in audits),
        "verdictCounts": dict(sorted(Counter(str(item.get("verdict") or "unknown") for item in audits).items())),
    }


def measurement_rendered_policy_fingerprint(
    item: dict[str, Any],
    *,
    bins: int = RENDERED_POLICY_FINGERPRINT_BINS,
) -> str:
    payload = rendered_policy_fingerprint_payload(item, bins=bins)
    if payload is None:
        return ""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _policy_alias_differences(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    policies_by_unit: dict[str, set[str]] = defaultdict(set)
    for item in items:
        unit_policies = (item.get("unitComposition") or {}).get("unitPolicies") or {}
        for unit_id, policy in unit_policies.items():
            policies_by_unit[str(unit_id)].add(str(policy))
    return [
        {
            "unitId": unit_id,
            "policies": sorted(policies),
            "reason": (
                "declared local policies produced the same cross-phase painted "
                "geometry fingerprint"
            ),
        }
        for unit_id, policies in sorted(policies_by_unit.items())
        if len(policies) > 1
    ]


def annotate_rendered_policy_equivalence(
    measurements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mark rendered-equivalent recursive candidates and choose representatives."""

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in measurements:
        fingerprint = measurement_rendered_policy_fingerprint(item)
        item["renderedPolicyFingerprint"] = fingerprint
        item["renderedPolicyFingerprintVersion"] = (
            RENDERED_POLICY_FINGERPRINT_VERSION if fingerprint else ""
        )
        item["pixelRectAudit"] = audit_authoritative_pixel_rect_coordinate_system(item)
        item["renderedEquivalenceExcludedFromRanking"] = bool(
            item.get("shadowOnly")
            and not item.get("responsiveEligible")
        )
        item["renderedEquivalentAliases"] = []
        item["renderedEquivalenceGroupSize"] = 1
        item["policyRealizationAliasDiagnostics"] = []
        if (
            fingerprint
            and not bool(item.get("shadowOnly"))
            and str(item.get("candidateMode") or "") == "recursive-composition"
        ):
            grouped[
                (
                    str(item.get("hierarchyId") or ""),
                    str(item.get("viewportProfile") or ""),
                    fingerprint,
                )
            ].append(item)

    summaries: list[dict[str, Any]] = []
    for (hierarchy_id, viewport_profile, fingerprint), group in sorted(
        grouped.items(),
        key=lambda entry: entry[0],
    ):
        representative = min(group, key=measurement_ranking_sort_key)
        representative_id = str(representative.get("candidate") or "")
        all_candidates = sorted(str(item.get("candidate") or "") for item in group)
        diagnostics = _policy_alias_differences(group)
        for item in group:
            item_id = str(item.get("candidate") or "")
            item["renderedEquivalenceRepresentative"] = representative_id
            item["renderedEquivalenceGroupSize"] = len(group)
            item["renderedEquivalentAliases"] = [
                candidate_id
                for candidate_id in all_candidates
                if candidate_id != item_id
            ]
            item["renderedEquivalenceExcludedFromRanking"] = (
                item is not representative
            )
            item["policyRealizationAliasDiagnostics"] = copy.deepcopy(diagnostics)

        if len(group) > 1:
            summaries.append(
                {
                    "hierarchyId": hierarchy_id,
                    "viewportProfile": viewport_profile,
                    "fingerprint": fingerprint,
                    "representative": representative_id,
                    "equivalentCandidates": all_candidates,
                    "equivalentAliases": [
                        candidate_id
                        for candidate_id in all_candidates
                        if candidate_id != representative_id
                    ],
                    "groupSize": len(group),
                    "policyAliasDiagnostics": diagnostics,
                }
            )
    return summaries


def rendered_policy_representatives(
    measurements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    annotate_rendered_policy_equivalence(measurements)
    return [
        item
        for item in measurements
        if not bool(item.get("renderedEquivalenceExcludedFromRanking"))
    ]


def measurement_score(item: dict[str, Any]) -> int:
    classification = item.get("classification") or {}
    return int(classification.get("selectionScore", classification.get("score", 0)) or 0)


def measurement_status(item: dict[str, Any]) -> str:
    return str((item.get("classification") or {}).get("status") or "fail")


def measurement_overlay_policy_count(item: dict[str, Any]) -> int:
    """Count composed local policies that intentionally consume overlay space."""

    composition = item.get("unitComposition") or {}
    policies = (composition.get("unitPolicies") or {}).values()
    return sum(
        1
        for policy in policies
        if layout_unit_ownership_spec(str(policy)).get("mode") == "overlay"
    )


def measurement_margin_evidence(item: dict[str, Any]) -> dict[str, float | int]:
    """Return unrounded Stage C evidence used after the integer score ties."""

    classification = item.get("classification") or {}
    phase_fit = item.get("phaseFit") or {}
    unit_fit = item.get("layoutUnitFit") or {}
    composition = item.get("unitComposition") or {}
    phase_rows = list(phase_fit.get("phases") or [])
    phase_measurements = list(item.get("phaseMeasurements") or [])

    declared_headroom = phase_fit.get("worstDominantHeadroom")
    if declared_headroom is None:
        headrooms = [
            float(row.get("dominantShare", 0) or 0)
            - float(row.get("minDominantShare", 0) or 0)
            for row in phase_rows
        ]
        worst_headroom = min(headrooms, default=-1.0)
    else:
        worst_headroom = float(declared_headroom)

    declared_raw_headroom = phase_fit.get("worstRawDominantHeadroom")
    if declared_raw_headroom is None:
        raw_headrooms = [
            float(
                row.get(
                    "dominantRawHeadroom",
                    float(row.get("dominantShare", 0) or 0)
                    - float(row.get("minDominantShare", 0) or 0),
                )
                or 0
            )
            for row in phase_rows
        ]
        worst_raw_headroom = min(raw_headrooms, default=worst_headroom)
    else:
        worst_raw_headroom = float(declared_raw_headroom)

    declared_variance = phase_fit.get("scoreVariance")
    if declared_variance is None:
        phase_scores = [
            float(row.get("rawScore", row.get("score", 0)) or 0)
            for row in phase_rows
        ]
        if phase_scores:
            mean_phase_score = sum(phase_scores) / len(phase_scores)
            phase_variance = sum(
                (score - mean_phase_score) ** 2 for score in phase_scores
            ) / len(phase_scores)
        else:
            phase_variance = 0.0
    else:
        phase_variance = float(declared_variance)

    occupancies = [
        float((phase_item.get("geometryFacts") or {}).get("usefulFocusOccupancy", 0) or 0)
        for phase_item in phase_measurements
    ]
    if not occupancies:
        occupancies = [
            float((item.get("geometryFacts") or {}).get("usefulFocusOccupancy", 0) or 0)
        ]

    return {
        "selectionScoreRaw": float(
            classification.get(
                "selectionScoreRaw",
                classification.get("selectionScore", classification.get("score", 0)),
            )
            or 0.0
        ),
        "worstPhaseHeadroom": float(worst_headroom),
        "worstPhaseRawHeadroom": float(worst_raw_headroom),
        "worstUnitScore": float(
            unit_fit.get("worstScoreRaw", unit_fit.get("worstScore", 100)) or 0.0
        ),
        "overlayPolicyCount": measurement_overlay_policy_count(item),
        "phaseScoreVariance": float(phase_variance),
        "effectiveFocusOccupancy": (
            sum(occupancies) / len(occupancies) if occupancies else 0.0
        ),
        "compositionPreflightScore": int(
            composition.get("preflightScore", -1)
            if composition.get("preflightScore") is not None
            else -1
        ),
    }


def measurement_ranking_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Sort best first while preserving every hard gate and status boundary."""

    evidence = measurement_margin_evidence(item)
    return (
        -measurement_score(item),
        measurement_quality_rank(measurement_status(item)),
        -float(evidence["worstPhaseHeadroom"]),
        -float(evidence["worstUnitScore"]),
        int(evidence["overlayPolicyCount"]),
        float(evidence["phaseScoreVariance"]),
        -float(evidence["effectiveFocusOccupancy"]),
        -int(evidence["compositionPreflightScore"]),
        str(item.get("candidate") or ""),
    )



RESPONSIVE_REMEDIATION_POLICY_LEVELS: dict[str, int] = {
    # Level 0 keeps large active regions simultaneously visible in exclusive partitions.
    "dominant-workflow-stack": 0,
    "phase-selector-unit": 0,
    "command-over-dominant": 0,
    "command-inline-header": 0,
    "shared-horizontal-band": 0,
    "bounded-side-drawer": 0,
    # Level 1 compacts or reflows a persistent region without replacing the active task.
    "compact-project-rail": 1,
    "side-command-rail": 1,
    "stacked-feedback": 1,
    "bounded-bottom-drawer": 1,
    # Level 2 uses inline staging or a bounded overlay because two large surfaces are tight.
    "selector-overlay": 2,
    "workflow-footer-overlay": 2,
    "inline-phase-stage": 2,
    "tabbed-phase-support": 2,
    "user-tab-workbench": 2,
    # Level 3 replaces inactive or competing surfaces with sequential triggers/stages.
    "sequential-phase-stage": 3,
    "one-active-plus-triggers": 3,
}

LEGACY_RESPONSIVE_REMEDIATION_LEVELS: dict[str, int] = {
    "split-pane": 0,
    "sectioned-sidebar": 0,
    "inspector": 0,
    "dashboard-grid": 0,
    "top-band-dominant-surface": 0,
    "source-order-stacked": 1,
    "focus-priority": 1,
    "bounded-drawer": 1,
    "selected-context-workflow": 1,
    "progressive-workflow": 2,
    "workflow-with-proof-drawer": 2,
    "top-band-focus-overlay": 2,
}

RESPONSIVE_REMEDIATION_LABELS = {
    0: "simultaneous-partition",
    1: "compact-or-reflow",
    2: "inline-or-bounded-overlay",
    3: "sequential-stage-replacement",
}


def derive_responsive_capacity_bands_from_hints(
    hierarchy: dict[str, Any],
) -> dict[str, Any]:
    """Derive capacity thresholds from the normalized layout-hint minima.

    The constants are small chrome/gap allowances, not application breakpoints.
    The application-specific part comes from the declared unit minimums.
    """

    contract = normalize_layout_hint_contract(hierarchy)
    units = {
        str(item.get("id") or ""): item
        for item in contract.get("units") or []
    }
    if contract.get("state") != "complete" or not units:
        return {
            "state": "unavailable",
            "bands": [
                {"id": "wide", "minWidth": 1320, "maxRemediationLevel": 0},
                {"id": "medium", "minWidth": 1040, "maxRemediationLevel": 1},
                {"id": "narrow", "minWidth": 760, "maxRemediationLevel": 2},
                {"id": "compact", "minWidth": 0, "maxRemediationLevel": 3},
            ],
            "inputs": {},
        }

    command = float((units.get("command-workflow") or {}).get("minInline", 520) or 520)
    support = float((units.get("phase-support") or {}).get("minInline", 300) or 300)
    identity = float((units.get("project-identity") or {}).get("minInline", 220) or 220)
    feedback = float((units.get("persistent-feedback") or {}).get("minInline", 420) or 420)
    chrome_allowance = 180.0
    gap_allowance = 48.0

    def ceil_track(value: float) -> int:
        return int(math.ceil(max(0.0, value) / 8.0) * 8)

    # A shared robustness factor reserves enough width for real text wrapping,
    # control chrome, and the required 1% phase-floor safety margin.  It is applied
    # to authored minima rather than acting as an application-specific breakpoint.
    robustness_factor = 1.368
    side_required = ceil_track(
        (command + support + chrome_allowance + gap_allowance) * robustness_factor
    )
    bottom_required = ceil_track(
        (max(command, feedback, support) + chrome_allowance + gap_allowance)
        * robustness_factor
    )
    tab_required = ceil_track(
        (max(support, identity) + chrome_allowance + gap_allowance) * 1.35
    )
    # Preserve a useful separation between adjacent bands even if future hints
    # declare surprisingly small minima.
    side_required = max(side_required, bottom_required + 240)
    bottom_required = max(bottom_required, tab_required + 200)
    tab_required = max(tab_required, 640)

    return {
        "state": "complete",
        "version": RESPONSIVE_PRESENTATION_CONTRACT_VERSION,
        "inputs": {
            "commandWorkflowMinInline": command,
            "phaseSupportMinInline": support,
            "projectIdentityMinInline": identity,
            "persistentFeedbackMinInline": feedback,
            "chromeAllowance": chrome_allowance,
            "gapAllowance": gap_allowance,
            "robustnessFactor": robustness_factor,
        },
        "bands": [
            {
                "id": "wide",
                "minWidth": side_required,
                "maxRemediationLevel": 0,
                "reason": "Authored center and right support minima fit simultaneously.",
            },
            {
                "id": "medium",
                "minWidth": bottom_required,
                "maxRemediationLevel": 1,
                "reason": "Center remains viable after support reflows to a bottom partition.",
            },
            {
                "id": "narrow",
                "minWidth": tab_required,
                "maxRemediationLevel": 2,
                "reason": "One large active tab fits with compact semantic summaries.",
            },
            {
                "id": "compact",
                "minWidth": 0,
                "maxRemediationLevel": 3,
                "reason": "Only sequential active-stage replacement is guaranteed.",
            },
        ],
    }


def responsive_contract_for_hierarchy(hierarchy: dict[str, Any]) -> dict[str, Any]:
    """Return a validated capacity contract for responsive policy selection."""

    declared = copy.deepcopy(hierarchy.get("responsiveContract") or {})
    derived_capacity = derive_responsive_capacity_bands_from_hints(hierarchy)
    contract = {
        "mode": "capacity-bands",
        "policyVersion": RESPONSIVE_POLICY_VERSION,
        "bands": [
            {"id": "wide", "minWidth": 1320, "maxRemediationLevel": 0},
            {"id": "medium", "minWidth": 1040, "maxRemediationLevel": 1},
            {"id": "narrow", "minWidth": 760, "maxRemediationLevel": 2},
            {"id": "compact", "minWidth": 0, "maxRemediationLevel": 3},
        ],
        "minimumRobustHeadroom": 0.01,
        "switchPenalty": 1.75,
        "unnecessaryRemediationPenalty": 12.0,
        "hysteresisPx": DEFAULT_RESPONSIVE_HYSTERESIS_PX,
        "semanticInvariants": [
            "active focus remains usable",
            "required active-phase companions remain attributable",
            "critical actions remain reachable",
            "no undeclared overlap or interception is introduced",
        ],
    }
    contract.update(
        {
            key: value
            for key, value in declared.items()
            if key not in {"bands", "phasePresentations"}
        }
    )
    if declared.get("phasePresentations"):
        contract["phasePresentations"] = copy.deepcopy(
            declared["phasePresentations"]
        )
    if (
        str(declared.get("mode") or "") == "derived-from-layout-hints"
        and derived_capacity.get("state") == "complete"
    ):
        contract["bands"] = copy.deepcopy(derived_capacity["bands"])
        contract["capacityDerivation"] = copy.deepcopy(derived_capacity)
    elif declared.get("bands"):
        contract["bands"] = copy.deepcopy(declared["bands"])

    bands: list[dict[str, Any]] = []
    for raw in contract.get("bands") or []:
        band = {
            "id": slugify(str(raw.get("id") or "band")),
            "minWidth": max(0, int(raw.get("minWidth", 0) or 0)),
            "maxRemediationLevel": max(
                0, min(3, int(raw.get("maxRemediationLevel", 0) or 0))
            ),
            "reason": str(raw.get("reason") or ""),
        }
        bands.append(band)
    if not bands:
        raise ValueError(
            f"Responsive contract for {hierarchy.get('id', '<unknown>')} has no bands."
        )
    bands.sort(key=lambda item: (-item["minWidth"], item["id"]))
    if bands[-1]["minWidth"] != 0:
        raise ValueError(
            f"Responsive contract for {hierarchy.get('id', '<unknown>')} "
            "must include a zero-width fallback band."
        )
    previous_level = -1
    for band in bands:
        level = int(band["maxRemediationLevel"])
        if level < previous_level:
            raise ValueError(
                f"Responsive remediation must be monotonic as width shrinks: {bands!r}"
            )
        previous_level = level
    contract["bands"] = bands
    contract["minimumRobustHeadroom"] = max(
        0.0, float(contract.get("minimumRobustHeadroom", 0.01) or 0.0)
    )
    contract["switchPenalty"] = max(
        0.0, float(contract.get("switchPenalty", 1.75) or 0.0)
    )
    contract["unnecessaryRemediationPenalty"] = max(
        0.0,
        float(contract.get("unnecessaryRemediationPenalty", 12.0) or 0.0),
    )
    contract["hysteresisPx"] = max(
        0, int(contract.get("hysteresisPx", DEFAULT_RESPONSIVE_HYSTERESIS_PX) or 0)
    )
    return contract


def responsive_capacity_band(
    contract: dict[str, Any],
    width: int,
) -> dict[str, Any]:
    for band in contract.get("bands") or []:
        if int(width) >= int(band.get("minWidth", 0) or 0):
            return copy.deepcopy(band)
    return copy.deepcopy((contract.get("bands") or [])[-1])


def measurement_remediation_evidence(item: dict[str, Any]) -> dict[str, Any]:
    """Describe how restrictive one realization is before viewport admissibility."""

    composition = item.get("unitComposition") or {}
    policies = {
        str(unit_id): str(policy)
        for unit_id, policy in (composition.get("unitPolicies") or {}).items()
    }
    details: list[dict[str, Any]] = []
    if policies:
        for unit_id, policy in sorted(policies.items()):
            level = int(RESPONSIVE_REMEDIATION_POLICY_LEVELS.get(policy, 1))
            details.append(
                {
                    "unitId": unit_id,
                    "policy": policy,
                    "level": level,
                    "label": RESPONSIVE_REMEDIATION_LABELS[level],
                }
            )
        level = max((entry["level"] for entry in details), default=0)
    else:
        candidate = str(item.get("candidate") or "")
        level = int(LEGACY_RESPONSIVE_REMEDIATION_LEVELS.get(candidate, 1))
        details.append(
            {
                "unitId": "legacy-application",
                "policy": candidate,
                "level": level,
                "label": RESPONSIVE_REMEDIATION_LABELS[level],
            }
        )
    return {
        "level": level,
        "label": RESPONSIVE_REMEDIATION_LABELS[level],
        "policies": details,
    }


def _responsive_option_quality(item: dict[str, Any]) -> float:
    evidence = measurement_margin_evidence(item)
    status_penalty = {"pass": 0.0, "watch": 2.5, "fail": 80.0}.get(
        measurement_status(item), 80.0
    )
    headroom = float(evidence["worstPhaseHeadroom"])
    headroom_bonus = max(-0.20, min(0.20, headroom)) * 24.0
    unit_bonus = (float(evidence["worstUnitScore"]) - 90.0) * 0.05
    return (
        float(evidence["selectionScoreRaw"])
        + headroom_bonus
        + unit_bonus
        - status_penalty
    )


def _responsive_profile_options(
    *,
    hierarchy: dict[str, Any],
    measurements: list[dict[str, Any]],
    profile: ViewportProfile,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Create feasibility-derived authored options for one measured viewport.

    Milestone 2.1 does not let general recursive candidates silently rescue holes
    in the authored hint chain.  Among hint-compiled candidates, the least
    restrictive robust realization wins.  A stronger realization becomes
    admissible only when all lower levels fail or lack the required safety margin.
    """

    band = responsive_capacity_band(contract, profile.width)
    hierarchy_measurements = [
        item
        for item in measurements
        if str(item.get("hierarchyId") or "") == str(hierarchy.get("id") or "")
    ]
    authored_mode = any(
        bool(item.get("responsiveEligible")) for item in hierarchy_measurements
    )
    representatives = [
        item
        for item in hierarchy_measurements
        if str(item.get("viewportProfile") or "") == profile.name
        and (
            bool(item.get("responsiveEligible"))
            if authored_mode
            else True
        )
        and not bool(item.get("renderedEquivalenceExcludedFromRanking"))
    ]

    option_rows: list[dict[str, Any]] = []
    for item in representatives:
        remediation = measurement_remediation_evidence(item)
        evidence = measurement_margin_evidence(item)
        option_rows.append(
            {
                "measurement": item,
                "candidate": str(item.get("candidate") or ""),
                "status": measurement_status(item),
                "score": measurement_score(item),
                "rawScore": float(evidence["selectionScoreRaw"]),
                "headroom": float(evidence["worstPhaseHeadroom"]),
                "rawHeadroom": float(
                    evidence.get(
                        "worstPhaseRawHeadroom",
                        evidence["worstPhaseHeadroom"],
                    )
                ),
                "worstUnitScore": float(evidence["worstUnitScore"]),
                "remediationLevel": int(remediation["level"]),
                "remediationLabel": str(remediation["label"]),
                "remediationPolicies": copy.deepcopy(remediation["policies"]),
                "declaredBandCompatible": int(remediation["level"])
                <= int(band["maxRemediationLevel"]),
                "quality": _responsive_option_quality(item),
            }
        )

    passing_rows = [
        row
        for row in option_rows
        if row["status"] in ACCEPTABLE_LAYOUT_STATUSES
    ]
    robust_headroom = float(contract["minimumRobustHeadroom"])
    robust_rows = [
        row
        for row in passing_rows
        if float(row["headroom"]) + PHASE_SHARE_FLOOR_TOLERANCE
        >= robust_headroom
    ]

    transition_gap = not passing_rows
    if robust_rows:
        selected_level = min(int(row["remediationLevel"]) for row in robust_rows)
        usable = [
            row
            for row in robust_rows
            if int(row["remediationLevel"]) == selected_level
        ]
        admissibility_reason = (
            "least restrictive browser-verified realization with robust headroom"
        )
    elif passing_rows:
        selected_level = min(int(row["remediationLevel"]) for row in passing_rows)
        usable = [
            row
            for row in passing_rows
            if int(row["remediationLevel"]) == selected_level
        ]
        admissibility_reason = (
            "least restrictive passing realization; robust headroom unavailable"
        )
    else:
        selected_level = min(
            (int(row["remediationLevel"]) for row in option_rows),
            default=3,
        )
        usable = sorted(
            option_rows,
            key=lambda row: measurement_ranking_sort_key(row["measurement"]),
        )[:1]
        admissibility_reason = "no authored realization passed"

    lower_level_rows = [
        row for row in option_rows if int(row["remediationLevel"]) < selected_level
    ]
    lower_level_failures = [
        {
            "candidate": row["candidate"],
            "level": int(row["remediationLevel"]),
            "status": row["status"],
            "headroom": float(row["headroom"]),
        }
        for row in lower_level_rows
        if row["status"] not in ACCEPTABLE_LAYOUT_STATUSES
        or float(row["headroom"]) + PHASE_SHARE_FLOOR_TOLERANCE
        < robust_headroom
    ]

    selected_candidates = {row["candidate"] for row in usable}
    selected_beyond_band = selected_level > int(band["maxRemediationLevel"])
    lower_failure_evidence = bool(lower_level_rows) and all(
        row["status"] not in ACCEPTABLE_LAYOUT_STATUSES
        or float(row["headroom"]) + PHASE_SHARE_FLOOR_TOLERANCE
        < robust_headroom
        for row in lower_level_rows
    )
    forced_beyond_band = bool(
        selected_beyond_band and not lower_failure_evidence
    )
    for row in option_rows:
        acceptable = row["status"] in ACCEPTABLE_LAYOUT_STATUSES
        at_selected_level = int(row["remediationLevel"]) == selected_level
        row["capacityAdmissible"] = bool(
            acceptable
            and at_selected_level
            and row["candidate"] in selected_candidates
            and not forced_beyond_band
        )
        row["unnecessaryRemediationLevels"] = max(
            0, int(row["remediationLevel"]) - int(selected_level)
        )
        row["forcedBeyondBand"] = bool(
            forced_beyond_band
            and at_selected_level
            and row["candidate"] in selected_candidates
        )
        row["transitionGap"] = transition_gap
        row["admissibilitySource"] = "browser-feasibility"
        row["admissibilityReason"] = admissibility_reason
        row["lowerLevelRejected"] = copy.deepcopy(lower_level_failures)
        item = row["measurement"]
        item["responsiveCapacityBand"] = band["id"]
        item["responsiveMaxRemediationLevel"] = int(band["maxRemediationLevel"])
        item["responsiveRemediation"] = {
            "level": row["remediationLevel"],
            "label": row["remediationLabel"],
            "policies": copy.deepcopy(row["remediationPolicies"]),
        }
        item["responsiveCapacityAdmissible"] = bool(row["capacityAdmissible"])
        item["responsiveUnnecessaryRemediationLevels"] = int(
            row["unnecessaryRemediationLevels"]
        )
        item["responsiveAdmissibilitySource"] = row["admissibilitySource"]
        item["responsiveAdmissibilityReason"] = row["admissibilityReason"]

    return {
        "profile": profile,
        "band": band,
        "options": usable,
        "allOptions": option_rows,
        "forcedBeyondBand": forced_beyond_band,
        "transitionGap": transition_gap,
        "baselineLevel": selected_level,
        "authoredMode": authored_mode,
        "admissibilitySource": "browser-feasibility",
        "admissibilityReason": admissibility_reason,
        "lowerLevelRejected": lower_level_failures,
    }

def _widest_transition_overlap_run(
    evidence_rows: list[dict[str, Any]],
    *,
    predicate_key: str,
    target_width: float,
) -> list[dict[str, Any]]:
    """Return the widest sampled contiguous overlap run around one transition.

    "Contiguous" means no sampled counterexample appears between the retained
    widths.  The browser proof remains sampled evidence; missing integer widths
    are not silently described as measured.
    """

    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in sorted(evidence_rows, key=lambda item: int(item["width"])):
        if bool(row.get(predicate_key)):
            current.append(row)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not runs:
        return []

    def run_key(run: list[dict[str, Any]]) -> tuple[float, int, float]:
        lower = int(run[0]["width"])
        upper = int(run[-1]["width"])
        span = upper - lower
        midpoint = (lower + upper) / 2.0
        return (float(span), len(run), -abs(midpoint - target_width))

    return max(runs, key=run_key)



def is_responsive_transition_evidence_profile(
    profile: ViewportProfile | str,
) -> bool:
    """Return whether a viewport exists only to prove a shared transition envelope."""

    name = profile.name if isinstance(profile, ViewportProfile) else str(profile)
    return name.startswith(RESPONSIVE_TRANSITION_EVIDENCE_PREFIX)


def authored_responsive_candidate_chain(
    profile_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Choose one authored realization per remediation level.

    Transition-proof probes are evidence about adjacent realizations.  They must
    not become independent local policy decisions.  This helper derives the
    ordered authored state graph once, using all measured evidence to select the
    strongest realization at each remediation level.
    """

    by_candidate: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for profile_row in profile_rows:
        for option in profile_row.get("allOptions") or []:
            candidate = str(option.get("candidate") or "")
            if not candidate:
                continue
            by_candidate[candidate].append((profile_row, option))

    candidate_rows: list[dict[str, Any]] = []
    robust_headroom = LAYOUT_HINT_MIN_ROBUST_HEADROOM
    for candidate, samples in by_candidate.items():
        levels = [
            int(option.get("remediationLevel", 0) or 0)
            for _, option in samples
        ]
        if not levels:
            continue
        level = min(levels)
        acceptable = [
            (profile_row, option)
            for profile_row, option in samples
            if str(option.get("status") or "") in ACCEPTABLE_LAYOUT_STATUSES
        ]
        robust = [
            (profile_row, option)
            for profile_row, option in acceptable
            if float(option.get("headroom", -1.0) or 0.0)
            + PHASE_SHARE_FLOOR_TOLERANCE
            >= robust_headroom
        ]
        positive = [
            (profile_row, option)
            for profile_row, option in acceptable
            if float(
                option.get(
                    "rawHeadroom",
                    option.get("headroom", -1.0),
                )
                or 0.0
            )
            > 0.0
        ]
        measured_widths = [
            int(profile_row["profile"].width)
            for profile_row, _ in samples
        ]
        passing_widths = [
            int(profile_row["profile"].width)
            for profile_row, _ in acceptable
        ]
        qualities = [
            float(option.get("quality", 0.0) or 0.0)
            for _, option in samples
        ]
        candidate_rows.append(
            {
                "candidate": candidate,
                "remediationLevel": level,
                "width": max(passing_widths or measured_widths or [0]),
                "measuredWidthMin": min(measured_widths or [0]),
                "measuredWidthMax": max(measured_widths or [0]),
                "acceptableSampleCount": len(acceptable),
                "robustSampleCount": len(robust),
                "positiveHeadroomSampleCount": len(positive),
                "meanQuality": (
                    sum(qualities) / len(qualities) if qualities else 0.0
                ),
            }
        )

    chain: list[dict[str, Any]] = []
    for level in sorted({int(item["remediationLevel"]) for item in candidate_rows}):
        candidates = [
            item for item in candidate_rows
            if int(item["remediationLevel"]) == level
        ]
        if not candidates:
            continue
        chosen = max(
            candidates,
            key=lambda item: (
                int(item["robustSampleCount"]),
                int(item["acceptableSampleCount"]),
                int(item["positiveHeadroomSampleCount"]),
                float(item["meanQuality"]),
                str(item["candidate"]),
            ),
        )
        chain.append(copy.deepcopy(chosen))

    levels = [int(item["remediationLevel"]) for item in chain]
    complete = bool(chain) and levels == list(range(levels[0], levels[-1] + 1))
    return {
        "version": RESPONSIVE_STATE_MACHINE_VERSION,
        "state": "complete" if complete else "incomplete",
        "candidates": chain,
        "levels": levels,
        "issues": (
            []
            if complete
            else [
                "authored responsive candidates do not form a contiguous remediation chain"
            ]
        ),
    }


def responsive_transition_evidence_rows(
    profile_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Serialize proof-only viewports separately from policy selections."""

    evidence: list[dict[str, Any]] = []
    for profile_row in profile_rows:
        profile = profile_row["profile"]
        if not is_responsive_transition_evidence_profile(profile):
            continue
        options = []
        for option in profile_row.get("allOptions") or []:
            options.append(
                {
                    "candidate": str(option.get("candidate") or ""),
                    "status": str(option.get("status") or ""),
                    "remediationLevel": int(
                        option.get("remediationLevel", 0) or 0
                    ),
                    "headroom": float(option.get("headroom", -1.0) or 0.0),
                    "rawHeadroom": float(
                        option.get(
                            "rawHeadroom",
                            option.get("headroom", -1.0),
                        )
                        or 0.0
                    ),
                }
            )
        evidence.append(
            {
                "viewportProfile": profile.name,
                "width": int(profile.width),
                "height": int(profile.height),
                "band": str((profile_row.get("band") or {}).get("id") or ""),
                "options": sorted(
                    options,
                    key=lambda item: (
                        int(item["remediationLevel"]),
                        str(item["candidate"]),
                    ),
                ),
            }
        )
    return sorted(evidence, key=lambda item: -int(item["width"]))


def select_authored_hysteretic_path(
    profile_rows: list[dict[str, Any]],
    *,
    candidate_chain: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply an ordered shrink path instead of re-ranking inside overlap regions."""

    policy_rows = [
        row
        for row in sorted(
            profile_rows,
            key=lambda item: (
                -int(item["profile"].width),
                -int(item["profile"].height),
            ),
        )
        if not is_responsive_transition_evidence_profile(row["profile"])
    ]
    if not candidate_chain:
        return [], {
            "version": RESPONSIVE_STATE_MACHINE_VERSION,
            "state": "fail",
            "reason": "No authored responsive candidate chain was available.",
            "candidateSequence": [],
            "transitionEvidenceExcluded": True,
            "missingSelections": [],
        }

    chain_by_candidate = {
        str(item["candidate"]): item for item in candidate_chain
    }
    transition_by_from = {
        str(item["fromCandidate"]): item for item in transitions
    }
    current = str(candidate_chain[0]["candidate"])
    selected: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    applied_switches: list[dict[str, Any]] = []

    for profile_row in policy_rows:
        profile = profile_row["profile"]
        width = int(profile.width)
        while current in transition_by_from:
            transition = transition_by_from[current]
            if width > int(transition["switchDownBelow"]):
                break
            next_candidate = str(transition["toCandidate"])
            applied_switches.append(
                {
                    "width": width,
                    "fromCandidate": current,
                    "toCandidate": next_candidate,
                    "threshold": int(transition["switchDownBelow"]),
                }
            )
            current = next_candidate

        matching = [
            option
            for option in profile_row.get("allOptions") or []
            if str(option.get("candidate") or "") == current
        ]
        if not matching:
            missing.append(
                {
                    "viewportProfile": profile.name,
                    "width": width,
                    "candidate": current,
                    "reason": "state-machine candidate was not rendered at this policy probe",
                }
            )
            fallback = list(profile_row.get("options") or [])
            if not fallback:
                continue
            option = copy.deepcopy(
                max(
                    fallback,
                    key=lambda item: (
                        float(item.get("quality", 0.0) or 0.0),
                        str(item.get("candidate") or ""),
                    ),
                )
            )
            option["transitionGap"] = True
            option["capacityAdmissible"] = False
            option["forcedBeyondBand"] = False
            option["stateMachineCandidateMissing"] = current
        else:
            option = copy.deepcopy(
                max(
                    matching,
                    key=lambda item: (
                        float(item.get("quality", 0.0) or 0.0),
                        str(item.get("candidate") or ""),
                    ),
                )
            )
            acceptable = (
                str(option.get("status") or "") in ACCEPTABLE_LAYOUT_STATUSES
            )
            level = int(option.get("remediationLevel", 0) or 0)
            max_level = int(
                (profile_row.get("band") or {}).get(
                    "maxRemediationLevel",
                    level,
                )
                or 0
            )
            option["capacityAdmissible"] = bool(
                acceptable and level <= max_level
            )
            option["forcedBeyondBand"] = bool(
                acceptable and level > max_level
            )
            option["transitionGap"] = not acceptable
            option["unnecessaryRemediationLevels"] = 0
            option["admissibilitySource"] = "ordered-hysteretic-state-machine"
            option["admissibilityReason"] = (
                "candidate retained until its verified shrink threshold was crossed"
            )
            option["lowerLevelRejected"] = []
            option["declaredBandCompatible"] = level <= max_level

        option["stateMachineCandidate"] = current
        option["stateMachineVersion"] = RESPONSIVE_STATE_MACHINE_VERSION
        option["stateMachineChainLevel"] = int(
            chain_by_candidate.get(current, {}).get(
                "remediationLevel",
                option.get("remediationLevel", 0),
            )
            or 0
        )
        selected.append(option)

    levels = [int(item.get("remediationLevel", 0) or 0) for item in selected]
    monotonic_violations = sum(
        int(current_level < previous_level)
        for previous_level, current_level in zip(levels, levels[1:])
    )
    candidate_sequence = [
        str(item.get("candidate") or "") for item in selected
    ]
    compressed_sequence = [
        candidate
        for index, candidate in enumerate(candidate_sequence)
        if index == 0 or candidate != candidate_sequence[index - 1]
    ]
    expected_sequence = [
        str(item["candidate"]) for item in candidate_chain
    ]
    return selected, {
        "version": RESPONSIVE_STATE_MACHINE_VERSION,
        "state": (
            "complete"
            if not missing
            and not monotonic_violations
            and compressed_sequence == expected_sequence
            else "fail"
        ),
        "candidateSequence": compressed_sequence,
        "expectedCandidateSequence": expected_sequence,
        "appliedSwitches": applied_switches,
        "monotonicViolationCount": monotonic_violations,
        "transitionEvidenceExcluded": True,
        "policyProbeCount": len(policy_rows),
        "missingSelections": missing,
    }


def responsive_transition_rules(
    selections: list[dict[str, Any]],
    *,
    hysteresis_px: int,
    profile_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build transitions from positive raw-headroom browser overlap.

    A tolerance-only pass proves that a state is not broken; it does not prove a
    safe resize envelope.  The declared hysteresis is certified only when both
    adjacent authored realizations have strictly positive *raw* phase headroom
    over a sampled interval at least that wide.
    """

    profile_rows = list(profile_rows or [])
    required_hysteresis = max(1, int(hysteresis_px))
    rules: list[dict[str, Any]] = []
    for previous, current in zip(selections, selections[1:]):
        if previous["candidate"] == current["candidate"]:
            continue

        from_candidate = str(previous["candidate"])
        to_candidate = str(current["candidate"])
        upper_width = int(previous["width"])
        lower_width = int(current["width"])
        target_width = (upper_width + lower_width) / 2.0

        evidence_rows: list[dict[str, Any]] = []
        for profile_row in profile_rows:
            by_candidate = {
                str(option.get("candidate") or ""): option
                for option in profile_row.get("allOptions") or []
            }
            left = by_candidate.get(from_candidate)
            right = by_candidate.get(to_candidate)
            if not left or not right:
                continue

            left_status_ok = (
                str(left.get("status") or "") in ACCEPTABLE_LAYOUT_STATUSES
            )
            right_status_ok = (
                str(right.get("status") or "") in ACCEPTABLE_LAYOUT_STATUSES
            )
            left_headroom = float(left.get("headroom", -1.0))
            right_headroom = float(right.get("headroom", -1.0))
            left_raw = float(left.get("rawHeadroom", left_headroom))
            right_raw = float(right.get("rawHeadroom", right_headroom))
            tolerance_overlap = bool(
                left_status_ok
                and right_status_ok
                and left_headroom >= -PHASE_SHARE_FLOOR_TOLERANCE
                and right_headroom >= -PHASE_SHARE_FLOOR_TOLERANCE
            )
            positive_overlap = bool(
                left_status_ok
                and right_status_ok
                and left_raw > 0.0
                and right_raw > 0.0
            )
            profile = profile_row["profile"]
            evidence_rows.append(
                {
                    "width": int(profile.width),
                    "profile": str(profile.name),
                    "toleranceOverlap": tolerance_overlap,
                    "positiveOverlap": positive_overlap,
                    "toleranceOnly": bool(
                        tolerance_overlap and not positive_overlap
                    ),
                    "fromHeadroom": left_headroom,
                    "toHeadroom": right_headroom,
                    "fromRawHeadroom": left_raw,
                    "toRawHeadroom": right_raw,
                }
            )

        tolerance_run = _widest_transition_overlap_run(
            evidence_rows,
            predicate_key="toleranceOverlap",
            target_width=target_width,
        )
        positive_run = _widest_transition_overlap_run(
            evidence_rows,
            predicate_key="positiveOverlap",
            target_width=target_width,
        )

        overlap_widths = [int(row["width"]) for row in tolerance_run]
        overlap_profiles = [str(row["profile"]) for row in tolerance_run]
        positive_widths = [int(row["width"]) for row in positive_run]
        positive_profiles = [str(row["profile"]) for row in positive_run]
        tolerance_only_widths = sorted(
            {
                int(row["width"])
                for row in evidence_rows
                if bool(row.get("toleranceOnly"))
            }
        )

        overlap_verified = (
            len(overlap_widths) >= 2
            and overlap_widths[-1] > overlap_widths[0]
        )
        positive_overlap_verified = (
            len(positive_widths) >= 2
            and positive_widths[-1] > positive_widths[0]
        )
        overlap_min = overlap_widths[0] if overlap_verified else 0
        overlap_max = overlap_widths[-1] if overlap_verified else 0
        positive_min = positive_widths[0] if positive_overlap_verified else 0
        positive_max = positive_widths[-1] if positive_overlap_verified else 0
        positive_span = (
            positive_max - positive_min if positive_overlap_verified else 0
        )
        hysteresis_requirement_met = bool(
            positive_overlap_verified
            and positive_span >= required_hysteresis
        )

        if positive_overlap_verified:
            available = max(1, positive_span)
            desired = (
                required_hysteresis
                if hysteresis_requirement_met
                else available
            )
            midpoint = (positive_min + positive_max) / 2.0
            down_below = int(math.floor(midpoint - desired / 2.0))
            up_above = int(math.ceil(midpoint + desired / 2.0))
            down_below = max(
                positive_min,
                min(positive_max - 1, down_below),
            )
            up_above = max(
                down_below + 1,
                min(positive_max, up_above),
            )
        elif overlap_verified:
            available = max(1, overlap_max - overlap_min)
            midpoint = (overlap_min + overlap_max) / 2.0
            down_below = int(math.floor(midpoint - available / 2.0))
            up_above = int(math.ceil(midpoint + available / 2.0))
            down_below = max(overlap_min, min(overlap_max - 1, down_below))
            up_above = max(down_below + 1, min(overlap_max, up_above))
        else:
            midpoint = (upper_width + lower_width) / 2.0
            down_below = int(math.floor(midpoint))
            up_above = int(math.ceil(midpoint))
            if up_above <= down_below:
                up_above = down_below + 1

        if hysteresis_requirement_met:
            transition_state = "verified"
            reason = (
                "both authored realizations have positive raw phase headroom "
                "across the required hysteresis envelope"
            )
        elif positive_overlap_verified:
            transition_state = "narrow-positive-overlap"
            reason = (
                "positive raw-headroom overlap exists, but it is narrower than "
                f"the required {required_hysteresis}px hysteresis"
            )
        elif overlap_verified:
            transition_state = "tolerance-only-overlap"
            reason = (
                "both realizations pass only through phase-floor tolerance; "
                "no positive raw-headroom envelope is verified"
            )
        else:
            transition_state = "unverified"
            reason = "no browser-verified shared passing interval exists"

        rules.append(
            {
                "fromCandidate": from_candidate,
                "toCandidate": to_candidate,
                "fromRemediationLevel": previous["remediationLevel"],
                "toRemediationLevel": current["remediationLevel"],
                "upperProbeWidth": upper_width,
                "lowerProbeWidth": lower_width,
                "switchDownBelow": down_below,
                "switchUpAbove": up_above,
                "hysteresisPx": up_above - down_below,
                "requiredHysteresisPx": required_hysteresis,
                "hysteresisRequirementMet": hysteresis_requirement_met,
                "transitionState": transition_state,
                "overlapVerified": overlap_verified,
                "overlapMinWidth": overlap_min,
                "overlapMaxWidth": overlap_max,
                "overlapProbeWidths": overlap_widths,
                "overlapProfiles": overlap_profiles,
                "positiveOverlapVerified": positive_overlap_verified,
                "positiveOverlapMinWidth": positive_min,
                "positiveOverlapMaxWidth": positive_max,
                "positiveOverlapWidthPx": positive_span,
                "positiveOverlapProbeWidths": positive_widths,
                "positiveOverlapProfiles": positive_profiles,
                "toleranceOnlyProbeWidths": tolerance_only_widths,
                "overlapEvidence": evidence_rows,
                "reason": reason,
            }
        )
    return rules

def responsive_presentation_evidence(item: dict[str, Any]) -> dict[str, Any]:
    """Summarize the capacity-relative semantic presentation for one candidate."""

    phases: list[dict[str, Any]] = []
    for phase_measurement in item.get("phaseMeasurements") or []:
        presentation = copy.deepcopy(
            phase_measurement.get("responsivePresentation")
            or (phase_measurement.get("phaseScenario") or {}).get(
                "responsivePresentation"
            )
            or {}
        )
        scenario = phase_measurement.get("phaseScenario") or {}
        phases.append(
            {
                "phase": str(phase_measurement.get("phase") or ""),
                "capacityBand": str(presentation.get("capacityBand") or ""),
                "presentationBand": str(presentation.get("presentationBand") or ""),
                "contractSource": str(presentation.get("contractSource") or ""),
                "presentationMode": str(presentation.get("mode") or ""),
                "dominantSlot": str(
                    scenario.get("dominantSlot")
                    or phase_measurement.get("focusSlot")
                    or ""
                ),
                "summarySlots": list(scenario.get("summarySlots") or []),
                "reachableSlots": list(scenario.get("reachableSlots") or []),
                "returnToSlot": str(scenario.get("returnToSlot") or ""),
            }
        )
    return {
        "version": RESPONSIVE_PRESENTATION_CONTRACT_VERSION,
        "phases": phases,
        "modes": sorted(
            {
                str(phase.get("presentationMode") or "")
                for phase in phases
                if phase.get("presentationMode")
            }
        ),
    }


def responsive_sampled_coverage(
    selections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn discrete probes into explicit sampled coverage intervals.

    These intervals are evidence between adjacent probes, not a claim that every
    unsampled browser width was rendered.  Invalid or inadmissible selections
    become uncovered intervals instead of being hidden by a numerical rank.
    """

    if not selections:
        return {
            "version": RESPONSIVE_COVERAGE_VERSION,
            "state": "notMeasured",
            "intervals": [],
            "uncoveredIntervals": [],
            "coverageComplete": False,
        }
    ordered = sorted(selections, key=lambda item: -int(item["width"]))
    boundaries = [
        int(round((int(left["width"]) + int(right["width"])) / 2.0))
        for left, right in zip(ordered, ordered[1:])
    ]
    intervals: list[dict[str, Any]] = []
    for index, item in enumerate(ordered):
        upper = (
            int(ordered[0]["width"])
            if index == 0
            else boundaries[index - 1]
        )
        lower = (
            int(ordered[-1]["width"])
            if index == len(ordered) - 1
            else boundaries[index]
        )
        admissible = bool(item.get("capacityAdmissible"))
        acceptable = str(item.get("status") or "") in ACCEPTABLE_LAYOUT_STATUSES
        valid = (
            acceptable
            and admissible
            and not bool(item.get("transitionGap"))
            and int(item.get("phaseFloorFailureCount", 0) or 0) == 0
        )
        intervals.append(
            {
                "minWidth": min(lower, upper),
                "maxWidth": max(lower, upper),
                "candidate": str(item.get("candidate") or ""),
                "band": str(item.get("band") or ""),
                "remediationLevel": int(item.get("remediationLevel", 0) or 0),
                "valid": valid,
                "reason": (
                    "sampled state is acceptable and capacity-admissible"
                    if valid
                    else (
                        "selected state is outside its capacity contract"
                        if not admissible
                        else "selected state failed a semantic or geometry gate"
                    )
                ),
            }
        )
    uncovered = [item for item in intervals if not item["valid"]]
    return {
        "version": RESPONSIVE_COVERAGE_VERSION,
        "state": "complete" if not uncovered else "gapped",
        "domain": {
            "minWidth": int(ordered[-1]["width"]),
            "maxWidth": int(ordered[0]["width"]),
        },
        "intervals": intervals,
        "uncoveredIntervals": uncovered,
        "coverageComplete": not uncovered,
        "note": (
            "Coverage is bounded by rendered probes and transition intervals; "
            "future adaptive sampling may refine a boundary further."
        ),
    }


def simulate_responsive_resize(
    selections: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    *,
    direction: str,
) -> dict[str, Any]:
    """Exercise the derived hysteresis state machine one pixel at a time."""

    if not selections:
        return {
            "direction": direction,
            "stable": False,
            "switches": [],
            "finalCandidate": "",
        }
    widest = int(selections[0]["width"])
    narrowest = int(selections[-1]["width"])
    switches: list[dict[str, Any]] = []
    if direction == "down":
        current = str(selections[0]["candidate"])
        widths = range(widest, narrowest - 1, -1)
        ordered_rules = list(transitions)
        next_index = 0
        for width in widths:
            while next_index < len(ordered_rules):
                rule = ordered_rules[next_index]
                if current != rule["fromCandidate"]:
                    next_index += 1
                    continue
                if width <= int(rule["switchDownBelow"]):
                    current = str(rule["toCandidate"])
                    switches.append({"width": width, "candidate": current})
                    next_index += 1
                    continue
                break
        expected = str(selections[-1]["candidate"])
    elif direction == "up":
        current = str(selections[-1]["candidate"])
        widths = range(narrowest, widest + 1)
        ordered_rules = list(reversed(transitions))
        next_index = 0
        for width in widths:
            while next_index < len(ordered_rules):
                rule = ordered_rules[next_index]
                if current != rule["toCandidate"]:
                    next_index += 1
                    continue
                if width >= int(rule["switchUpAbove"]):
                    current = str(rule["fromCandidate"])
                    switches.append({"width": width, "candidate": current})
                    next_index += 1
                    continue
                break
        expected = str(selections[0]["candidate"])
    else:
        raise ValueError("direction must be 'down' or 'up'")

    candidate_sequence = [
        str(selections[0 if direction == "down" else -1]["candidate"]),
        *[str(item["candidate"]) for item in switches],
    ]
    oscillates = any(
        candidate in candidate_sequence[:index]
        for index, candidate in enumerate(candidate_sequence[1:], start=1)
        if candidate != candidate_sequence[index - 1]
    )
    return {
        "direction": direction,
        "stable": current == expected and not oscillates,
        "switches": switches,
        "finalCandidate": current,
        "expectedFinalCandidate": expected,
        "oscillationDetected": oscillates,
    }


def responsive_policy_for_hierarchy(
    *,
    hierarchy: dict[str, Any],
    measurements: list[dict[str, Any]],
    profiles: list[ViewportProfile],
    hysteresis_px: int | None = None,
) -> dict[str, Any]:
    """Resolve one capacity-admissible responsive policy across resize probes.

    Authored layouts use an ordered hysteretic state machine.  Shared-width
    transition probes contribute evidence to the transition graph but are never
    treated as autonomous local winners.
    """

    contract = responsive_contract_for_hierarchy(hierarchy)
    if hysteresis_px is not None:
        contract["hysteresisPx"] = max(0, int(hysteresis_px))
    available_profiles = [
        profile
        for profile in sorted(profiles, key=lambda item: (-item.width, -item.height))
        if any(
            str(item.get("hierarchyId") or "") == str(hierarchy.get("id") or "")
            and str(item.get("viewportProfile") or "") == profile.name
            for item in measurements
        )
    ]
    all_profile_rows = [
        _responsive_profile_options(
            hierarchy=hierarchy,
            measurements=measurements,
            profile=profile,
            contract=contract,
        )
        for profile in available_profiles
    ]
    if not all_profile_rows:
        return {
            "hierarchyId": hierarchy.get("id", ""),
            "state": "notMeasured",
            "policyVersion": RESPONSIVE_POLICY_VERSION,
            "stateMachineVersion": RESPONSIVE_STATE_MACHINE_VERSION,
            "selections": [],
            "transitions": [],
        }

    authored_mode = any(bool(row.get("authoredMode")) for row in all_profile_rows)
    transition_evidence = responsive_transition_evidence_rows(all_profile_rows)
    candidate_chain_report: dict[str, Any] = {
        "version": RESPONSIVE_STATE_MACHINE_VERSION,
        "state": "notApplicable",
        "candidates": [],
        "levels": [],
        "issues": [],
    }
    stateful_path: dict[str, Any] = {
        "version": RESPONSIVE_STATE_MACHINE_VERSION,
        "state": "notApplicable",
        "candidateSequence": [],
        "transitionEvidenceExcluded": False,
        "missingSelections": [],
    }

    if authored_mode:
        candidate_chain_report = authored_responsive_candidate_chain(
            all_profile_rows
        )
        candidate_chain = list(candidate_chain_report.get("candidates") or [])
        if candidate_chain_report.get("state") != "complete":
            return {
                "hierarchyId": hierarchy.get("id", ""),
                "state": "fail",
                "policyVersion": RESPONSIVE_POLICY_VERSION,
                "stateMachineVersion": RESPONSIVE_STATE_MACHINE_VERSION,
                "reason": "Authored responsive candidates did not form a complete ordered chain.",
                "candidateChain": candidate_chain_report,
                "transitionEvidence": transition_evidence,
                "transitionEvidenceCount": len(transition_evidence),
                "selections": [],
                "transitions": [],
            }
        transitions = responsive_transition_rules(
            candidate_chain,
            hysteresis_px=int(contract["hysteresisPx"]),
            profile_rows=all_profile_rows,
        )
        selected_options, stateful_path = select_authored_hysteretic_path(
            all_profile_rows,
            candidate_chain=candidate_chain,
            transitions=transitions,
        )
        profile_rows = [
            row
            for row in sorted(
                all_profile_rows,
                key=lambda item: (
                    -int(item["profile"].width),
                    -int(item["profile"].height),
                ),
            )
            if not is_responsive_transition_evidence_profile(row["profile"])
        ]
        monotonic_violations = int(
            stateful_path.get("monotonicViolationCount", 0) or 0
        )
    else:
        profile_rows = all_profile_rows
        # Generic synthetic hierarchies retain bounded dynamic programming.
        paths: dict[str, tuple[float, list[dict[str, Any]], int]] = {}
        for index, profile_row in enumerate(profile_rows):
            next_paths: dict[str, tuple[float, list[dict[str, Any]], int]] = {}
            for option in profile_row["options"]:
                local_cost = 100.0 - float(option["quality"])
                local_cost += (
                    float(contract["unnecessaryRemediationPenalty"])
                    * int(option["unnecessaryRemediationLevels"])
                )
                if option["forcedBeyondBand"]:
                    local_cost += 24.0
                if option["transitionGap"]:
                    local_cost += 120.0
                if index == 0:
                    next_paths[option["candidate"]] = (
                        local_cost,
                        [option],
                        0,
                    )
                    continue
                for previous_cost, previous_path, previous_violations in paths.values():
                    previous = previous_path[-1]
                    monotonic_violation = (
                        int(option["remediationLevel"])
                        < int(previous["remediationLevel"])
                    )
                    transition_cost = (
                        0.0
                        if option["candidate"] == previous["candidate"]
                        else float(contract["switchPenalty"])
                    )
                    if monotonic_violation:
                        transition_cost += 80.0
                    candidate_cost = previous_cost + local_cost + transition_cost
                    candidate_violations = previous_violations + int(
                        monotonic_violation
                    )
                    existing = next_paths.get(option["candidate"])
                    proposal = (
                        candidate_cost,
                        [*previous_path, option],
                        candidate_violations,
                    )
                    if existing is None or (
                        proposal[0],
                        proposal[2],
                        option["candidate"],
                    ) < (
                        existing[0],
                        existing[2],
                        existing[1][-1]["candidate"],
                    ):
                        next_paths[option["candidate"]] = proposal
            paths = next_paths

        if not paths:
            return {
                "hierarchyId": hierarchy.get("id", ""),
                "state": "fail",
                "policyVersion": RESPONSIVE_POLICY_VERSION,
                "stateMachineVersion": RESPONSIVE_STATE_MACHINE_VERSION,
                "reason": "No responsive candidate path was available.",
                "selections": [],
                "transitions": [],
            }

        _, selected_options, monotonic_violations = min(
            paths.values(),
            key=lambda entry: (
                entry[2],
                entry[0],
                entry[1][-1]["candidate"],
            ),
        )
        transitions = []

    if len(profile_rows) != len(selected_options):
        return {
            "hierarchyId": hierarchy.get("id", ""),
            "state": "fail",
            "policyVersion": RESPONSIVE_POLICY_VERSION,
            "stateMachineVersion": RESPONSIVE_STATE_MACHINE_VERSION,
            "reason": "Responsive state-machine selections did not cover every policy probe.",
            "candidateChain": candidate_chain_report,
            "statefulPath": stateful_path,
            "transitionEvidence": transition_evidence,
            "transitionEvidenceCount": len(transition_evidence),
            "selections": [],
            "transitions": transitions,
        }

    selections: list[dict[str, Any]] = []
    for profile_row, option in zip(profile_rows, selected_options):
        profile = profile_row["profile"]
        item = option["measurement"]
        item["responsivePolicySelected"] = True
        selections.append(
            {
                "viewportProfile": profile.name,
                "width": profile.width,
                "height": profile.height,
                "band": profile_row["band"]["id"],
                "maxRemediationLevel": int(
                    profile_row["band"]["maxRemediationLevel"]
                ),
                "candidate": option["candidate"],
                "status": option["status"],
                "score": option["score"],
                "rawScore": option["rawScore"],
                "worstPhaseHeadroom": option["headroom"],
                "worstPhaseRawHeadroom": option.get(
                    "rawHeadroom",
                    option["headroom"],
                ),
                "worstUnitScore": option["worstUnitScore"],
                "remediationLevel": option["remediationLevel"],
                "remediationLabel": option["remediationLabel"],
                "capacityAdmissible": option["capacityAdmissible"],
                "forcedBeyondBand": option["forcedBeyondBand"],
                "admissibilitySource": option.get(
                    "admissibilitySource", "browser-feasibility"
                ),
                "admissibilityReason": option.get("admissibilityReason", ""),
                "lowerLevelRejected": copy.deepcopy(
                    option.get("lowerLevelRejected") or []
                ),
                "declaredBandCompatible": bool(
                    option.get("declaredBandCompatible", False)
                ),
                "unnecessaryRemediationLevels": option[
                    "unnecessaryRemediationLevels"
                ],
                "transitionGap": option["transitionGap"],
                "phaseFloorFailureCount": int(
                    (item.get("phaseFit") or {}).get("phaseFloorFailureCount", 0)
                    or 0
                ),
                "capacityAdmissibilityState": (
                    "admissible"
                    if option["capacityAdmissible"]
                    else ("forced" if option["forcedBeyondBand"] else "invalid")
                ),
                "responsivePresentation": responsive_presentation_evidence(item),
                "responsiveEligibleShadow": bool(item.get("responsiveEligible")),
                "snapshots": copy.deepcopy(item.get("phaseSnapshots") or {}),
                "stateMachineCandidate": str(
                    option.get("stateMachineCandidate")
                    or option.get("candidate")
                    or ""
                ),
                "stateMachineVersion": str(
                    option.get("stateMachineVersion")
                    or RESPONSIVE_STATE_MACHINE_VERSION
                ),
            }
        )

    if not authored_mode:
        transitions = responsive_transition_rules(
            selections,
            hysteresis_px=int(contract["hysteresisPx"]),
            profile_rows=all_profile_rows,
        )

    coverage = responsive_sampled_coverage(selections)
    down = simulate_responsive_resize(
        selections, transitions, direction="down"
    )
    up = simulate_responsive_resize(selections, transitions, direction="up")
    gaps = [item for item in selections if item["transitionGap"]]
    forced = [item for item in selections if item["forcedBeyondBand"]]
    unverified_transitions = [
        item
        for item in transitions
        if authored_mode and not bool(item.get("positiveOverlapVerified"))
    ]
    insufficient_hysteresis_transitions = [
        item
        for item in transitions
        if authored_mode
        and bool(item.get("positiveOverlapVerified"))
        and not bool(item.get("hysteresisRequirementMet"))
    ]
    unnecessary = [
        item for item in selections if item["unnecessaryRemediationLevels"] > 0
    ]
    floor_failures = sum(item["phaseFloorFailureCount"] for item in selections)
    all_acceptable = all(
        item["status"] in ACCEPTABLE_LAYOUT_STATUSES for item in selections
    )
    headrooms = [float(item["worstPhaseHeadroom"]) for item in selections]
    width_gaps = [
        selections[index]["width"] - selections[index + 1]["width"]
        for index in range(len(selections) - 1)
    ]
    state_machine_failed = bool(
        authored_mode and stateful_path.get("state") != "complete"
    )
    state = "pass"
    if (
        gaps
        or floor_failures
        or monotonic_violations
        or unverified_transitions
        or not all_acceptable
        or not coverage["coverageComplete"]
        or not down["stable"]
        or not up["stable"]
        or state_machine_failed
    ):
        state = "fail"
    elif (
        forced
        or unnecessary
        or insufficient_hysteresis_transitions
        or min(headrooms, default=0.0)
        < float(contract["minimumRobustHeadroom"])
    ):
        state = "watch"

    return {
        "hierarchyId": hierarchy.get("id", ""),
        "state": state,
        "policyVersion": RESPONSIVE_POLICY_VERSION,
        "stateMachineVersion": RESPONSIVE_STATE_MACHINE_VERSION,
        "selectionMode": (
            "authored-hint-hysteretic-state-machine"
            if authored_mode
            else "generic-responsive"
        ),
        "contract": contract,
        "semanticContractStable": bool(
            all_acceptable
            and not floor_failures
            and not gaps
            and not forced
            and not unverified_transitions
            and coverage["coverageComplete"]
            and not state_machine_failed
        ),
        "candidateChain": candidate_chain_report,
        "statefulPath": stateful_path,
        "transitionEvidence": transition_evidence,
        "transitionEvidenceCount": len(transition_evidence),
        "policyProbeCount": len(selections),
        "selections": selections,
        "transitions": transitions,
        "sampledCoverage": coverage,
        "coverageComplete": bool(coverage["coverageComplete"]),
        "uncoveredIntervalCount": len(coverage["uncoveredIntervals"]),
        "resizeSimulation": {"down": down, "up": up},
        "transitionGapCount": len(gaps),
        "forcedBeyondBandCount": len(forced),
        "unverifiedTransitionCount": len(unverified_transitions),
        "insufficientHysteresisTransitionCount": len(
            insufficient_hysteresis_transitions
        ),
        "minimumVerifiedHysteresisPx": min(
            (
                int(item.get("positiveOverlapWidthPx", 0) or 0)
                for item in transitions
                if bool(item.get("positiveOverlapVerified"))
            ),
            default=0,
        ),
        "unnecessaryRemediationCount": len(unnecessary),
        "monotonicViolationCount": int(monotonic_violations),
        "switchCount": len(transitions),
        "worstViewportPhaseHeadroom": min(headrooms, default=-1.0),
        "meanViewportPhaseHeadroom": (
            sum(headrooms) / len(headrooms) if headrooms else -1.0
        ),
        "maxProbeGapPx": max(width_gaps, default=0),
        "probeCount": len(selections),
        "wideToNarrowStable": bool(down["stable"]),
        "narrowToWideStable": bool(up["stable"]),
    }


def build_responsive_policies(
    *,
    hierarchies: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    profiles_by_hierarchy: dict[str, list[ViewportProfile]],
    responsive_mode: str,
    hysteresis_px: int,
) -> list[dict[str, Any]]:
    mode = str(responsive_mode or "off").lower()
    if mode == "off":
        return []
    policies: list[dict[str, Any]] = []
    for hierarchy in hierarchies:
        if mode == "recursive" and not hierarchy.get("layoutUnitTree"):
            continue
        profiles = profiles_by_hierarchy.get(str(hierarchy.get("id") or ""), [])
        policy = responsive_policy_for_hierarchy(
            hierarchy=hierarchy,
            measurements=measurements,
            profiles=profiles,
            hysteresis_px=hysteresis_px,
        )
        policies.append(policy)
    return policies


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
    painted = facts.get("paintedOwnership") or facts.get("paintedOwnershipShadow") or {}
    highest_classification = highest_scoring.get("classification") or {}
    margin_evidence = measurement_margin_evidence(item)
    row = {
        "hierarchyId": item["hierarchyId"],
        "viewportProfile": item["viewportProfile"],
        "candidate": item["candidate"],
        "candidateMode": item.get("candidateMode", "legacy-family"),
        "responsivePolicySelected": bool(item.get("responsivePolicySelected")),
        "responsiveCapacityBand": item.get("responsiveCapacityBand", ""),
        "responsiveMaxRemediationLevel": item.get(
            "responsiveMaxRemediationLevel"
        ),
        "responsiveRemediation": copy.deepcopy(
            item.get("responsiveRemediation") or {}
        ),
        "responsiveCapacityAdmissible": item.get(
            "responsiveCapacityAdmissible"
        ),
        "responsiveUnnecessaryRemediationLevels": int(
            item.get("responsiveUnnecessaryRemediationLevels", 0) or 0
        ),
        "renderedPolicyFingerprint": item.get("renderedPolicyFingerprint", ""),
        "renderedPolicyFingerprintVersion": item.get(
            "renderedPolicyFingerprintVersion", ""
        ),
        "renderedEquivalenceRepresentative": item.get(
            "renderedEquivalenceRepresentative", item.get("candidate", "")
        ),
        "renderedEquivalenceGroupSize": int(
            item.get("renderedEquivalenceGroupSize", 1) or 1
        ),
        "renderedEquivalentAliases": list(
            item.get("renderedEquivalentAliases") or []
        ),
        "policyRealizationAliasDiagnostics": copy.deepcopy(
            item.get("policyRealizationAliasDiagnostics") or []
        ),
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
        "rootUnclaimedAreaRatio": facts.get(
            "rootUnclaimedAreaRatio", facts.get("unclaimedAreaRatio", 0)
        ),
        "activePresentationMode": facts.get(
            "activePresentationMode", "root-fallback"
        ),
        "activePresentationUnitId": facts.get("activePresentationUnitId", ""),
        "activePresentationOccupancy": facts.get(
            "activePresentationOccupancy",
            max(0.0, 1.0 - float(facts.get("unclaimedAreaRatio", 0) or 0)),
        ),
        "activePresentationUnclaimedRatio": facts.get(
            "activePresentationUnclaimedRatio",
            facts.get("unclaimedAreaRatio", 0),
        ),
        "intentionalInactiveRootRatio": facts.get(
            "intentionalInactiveRootRatio", 0
        ),
        "accidentalUnclaimedRootRatio": facts.get(
            "accidentalUnclaimedRootRatio",
            facts.get("unclaimedAreaRatio", 0),
        ),
        "focusShare": facts.get("focusShare", 0),
        "paintedOwnershipMode": facts.get("paintedOwnershipMode", "unavailable"),
        "paintedOwnershipEnforced": bool(
            facts.get("paintedOwnershipEnforced", False)
        ),
        "rawFocusShare": facts.get(
            "rawFocusShare", painted.get("rawFocusShare", facts.get("focusShare", 0))
        ),
        "effectiveFocusShare": facts.get(
            "effectiveFocusShare",
            painted.get("effectiveFocusShare", facts.get("focusShare", 0)),
        ),
        "focusOccludedShare": facts.get(
            "focusOccludedShare", painted.get("focusOccludedShare", 0)
        ),
        "exclusiveOwnedShare": facts.get(
            "exclusiveOwnedShare", painted.get("exclusiveOwnedShare", 0)
        ),
        "doubleClaimedShare": facts.get(
            "doubleClaimedShare", painted.get("doubleClaimedShare", 0)
        ),
        "declaredOverlayShare": facts.get(
            "declaredOverlayShare", painted.get("declaredOverlayShare", 0)
        ),
        "undeclaredPartitionOverlapShare": facts.get(
            "undeclaredPartitionOverlapShare",
            painted.get("undeclaredPartitionOverlapShare", 0),
        ),
        "partitionOverlapCellShare": facts.get(
            "partitionOverlapCellShare",
            painted.get("partitionOverlapCellShare", 0),
        ),
        "blockedCriticalControlCount": facts.get(
            "blockedCriticalControlCount", 0
        ),
        "foreignInterceptedCriticalControlCount": facts.get(
            "foreignInterceptedCriticalControlCount",
            facts.get("foreignInterceptedCriticalControlCountShadow", 0),
        ),
        "controlInterceptionOutcomeTotals": facts.get(
            "controlInterceptionOutcomeTotals",
            facts.get("controlInterceptionOutcomeTotalsShadow", {}),
        ),
        "paintedOwnershipOverlapMatrix": painted.get("overlapMatrix", []),
        "partitionOverlapByUnit": painted.get("partitionOverlapByUnit", []),
        "undeclaredPartitionOverlapMatrix": painted.get(
            "undeclaredPartitionOverlapMatrix", []
        ),
        "overlayBudgetExceeded": painted.get("overlayBudgetExceeded", []),
        "effectiveFocusShareShadow": painted.get(
            "effectiveFocusShare", facts.get("focusShare", 0)
        ),
        "focusOccludedShareShadow": painted.get(
            "focusOccludedShare", 0
        ),
        "exclusiveOwnedShareShadow": painted.get(
            "exclusiveOwnedShare", 0
        ),
        "doubleClaimedShareShadow": painted.get(
            "doubleClaimedShare", 0
        ),
        "declaredOverlayShareShadow": painted.get(
            "declaredOverlayShare", 0
        ),
        "undeclaredPartitionOverlapShareShadow": painted.get(
            "undeclaredPartitionOverlapShare", 0
        ),
        "partitionOverlapCellShareShadow": painted.get(
            "partitionOverlapCellShare", 0
        ),
        "partitionOverlapByUnitShadow": painted.get(
            "partitionOverlapByUnit", []
        ),
        "undeclaredPartitionOverlapMatrixShadow": painted.get(
            "undeclaredPartitionOverlapMatrix", []
        ),
        "overlayBudgetExceededShadow": painted.get(
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
        "paintedOwnershipOverlapMatrixShadow": painted.get(
            "overlapMatrix", []
        ),
        "desiredFocusShare": facts.get("desiredFocusShare", 0),
        "usefulFocusOccupancy": facts.get("usefulFocusOccupancy", 0),
        "companionProximityScore": facts.get("companionProximityScore", 1),
        "geometryScore": classification.get("geometryScore", classification.get("score", 0)),
        "selectionScore": classification.get("selectionScore", classification.get("score", 0)),
        "selectionScoreRaw": classification.get(
            "selectionScoreRaw",
            classification.get("selectionScore", classification.get("score", 0)),
        ),
        "selectionMarginEvidence": margin_evidence,
        "contractFitScore": classification.get("contractFitScore", 0),
        "contractFitState": classification.get("contractFitState", "notEvaluated"),
        "affordanceFitScore": classification.get("affordanceFitScore", 0),
        "affordanceFitState": classification.get("affordanceFitState", "notEvaluated"),
        "phaseFitScore": classification.get("phaseFitScore", 0),
        "phaseFitState": classification.get("phaseFitState", "notEvaluated"),
        "phaseWorstScore": phase_fit.get("worstScore", 0),
        "phaseWorstScoreRaw": phase_fit.get(
            "worstScoreRaw", phase_fit.get("worstScore", 0)
        ),
        "phaseMeanScore": phase_fit.get("meanScore", 0),
        "phaseMeanScoreRaw": phase_fit.get(
            "meanScoreRaw", phase_fit.get("meanScore", 0)
        ),
        "phaseSelectedDefaultScore": phase_fit.get("selectedDefaultScore", 0),
        "phaseSelectedDefaultScoreRaw": phase_fit.get(
            "selectedDefaultScoreRaw", phase_fit.get("selectedDefaultScore", 0)
        ),
        "phaseWorstDominantHeadroom": phase_fit.get(
            "worstDominantHeadroom", -1.0
        ),
        "phaseMeanDominantHeadroom": phase_fit.get(
            "meanDominantHeadroom", -1.0
        ),
        "phaseSelectedDefaultHeadroom": phase_fit.get(
            "selectedDefaultHeadroom", -1.0
        ),
        "phaseWorstRawDominantHeadroom": phase_fit.get(
            "worstRawDominantHeadroom",
            phase_fit.get("worstDominantHeadroom", -1.0),
        ),
        "phaseFloorTolerance": phase_fit.get(
            "phaseFloorTolerance", PHASE_SHARE_FLOOR_TOLERANCE
        ),
        "phaseFloorFailureCount": phase_fit.get("phaseFloorFailureCount", 0),
        "phaseScoreVariance": phase_fit.get("scoreVariance", 0.0),
        "phaseScoreStdDev": phase_fit.get("scoreStdDev", 0.0),
        "phaseSelectedDefaultPhase": phase_fit.get("selectedDefaultPhase", ""),
        "phaseHardFailureCount": phase_fit.get("hardFailureCount", 0),
        "layoutUnitFitScore": classification.get("layoutUnitFitScore", 100),
        "layoutUnitFitState": classification.get("layoutUnitFitState", "notDeclared"),
        "layoutUnitWorstScore": unit_fit.get("worstScore", 100),
        "layoutUnitWorstScoreRaw": unit_fit.get(
            "worstScoreRaw", unit_fit.get("worstScore", 100)
        ),
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


def best_by_hierarchy_viewport_rows(
    measurements: list[dict[str, Any]],
    responsive_policies: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    annotate_rendered_policy_equivalence(measurements)
    responsive_selected = {
        (
            str(policy.get("hierarchyId") or ""),
            str(selection.get("viewportProfile") or ""),
        ): str(selection.get("candidate") or "")
        for policy in (responsive_policies or [])
        for selection in (policy.get("selections") or [])
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for measurement in measurements:
        key = f"{measurement['hierarchyId']}::{measurement['viewportProfile']}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        if not bool(measurement.get("renderedEquivalenceExcludedFromRanking")):
            grouped[key].append(measurement)

    rows: list[dict[str, Any]] = []
    for key in order:
        ranked = sorted(
            grouped[key],
            key=measurement_ranking_sort_key,
        )
        highest_scoring = ranked[0]
        hierarchy_id, viewport_profile = key.split("::", 1)
        responsive_candidate = responsive_selected.get(
            (hierarchy_id, viewport_profile)
        )
        responsive_item = next(
            (
                item
                for item in ranked
                if str(item.get("candidate") or "") == responsive_candidate
            ),
            None,
        )
        if responsive_item is not None:
            selection_state = (
                "responsivePolicySelection"
                if measurement_status(responsive_item) in ACCEPTABLE_LAYOUT_STATUSES
                else "responsiveTransitionGap"
            )
            rows.append(
                measurement_selection_row(
                    responsive_item,
                    selection_state=selection_state,
                    highest_scoring=highest_scoring,
                )
            )
            continue

        acceptable = [
            item
            for item in ranked
            if measurement_status(item) in ACCEPTABLE_LAYOUT_STATUSES
        ]
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
    responsive_mode: str = DEFAULT_RESPONSIVE_MODE,
    responsive_viewports: list[ViewportProfile] | None = None,
    responsive_hysteresis_px: int = DEFAULT_RESPONSIVE_HYSTERESIS_PX,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - exercised only without dependency
        raise SystemExit(playwright_missing_message(exc)) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    measurements: list[dict[str, Any]] = []
    snapshot_files: list[str] = []
    generated_at = datetime.now(timezone.utc).isoformat()
    responsive_mode = str(responsive_mode or "off").lower()
    if responsive_mode not in {"off", "recursive", "all"}:
        raise ValueError(
            "responsive_mode must be one of: off, recursive, all"
        )
    responsive_viewports = list(responsive_viewports or [])
    profiles_by_hierarchy = {
        str(hierarchy.get("id") or ""): responsive_viewports_for_hierarchy(
            hierarchy,
            base_profiles=viewports,
            responsive_profiles=responsive_viewports,
            responsive_mode=responsive_mode,
        )
        for hierarchy in hierarchies
    }
    all_viewports: list[ViewportProfile] = []
    seen_viewports: set[tuple[str, int, int]] = set()
    for profiles in profiles_by_hierarchy.values():
        for profile in profiles:
            key = (profile.name, profile.width, profile.height)
            if key in seen_viewports:
                continue
            seen_viewports.add(key)
            all_viewports.append(profile)
    all_viewports.sort(key=lambda item: (-item.width, -item.height, item.name))

    with tempfile.TemporaryDirectory(prefix="flog-layout-trials-") as temp_name:
        temp_dir = Path(temp_name)
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - depends on local browser install
                raise SystemExit(playwright_missing_message(exc)) from exc

            try:
                for viewport in all_viewports:
                    context = browser.new_context(
                        viewport={"width": viewport.width, "height": viewport.height},
                        device_scale_factor=1,
                    )
                    page = context.new_page()
                    page.on("console", lambda msg: None)
                    for hierarchy in hierarchies:
                        allowed_profiles = profiles_by_hierarchy.get(
                            str(hierarchy.get("id") or ""), []
                        )
                        if not any(
                            profile.name == viewport.name
                            and profile.width == viewport.width
                            and profile.height == viewport.height
                            for profile in allowed_profiles
                        ):
                            continue
                        scenarios = phase_trial_scenarios(hierarchy)
                        multi_phase = len(scenarios) > 1
                        canonical_phase = str(
                            canonical_phase_scenario(hierarchy).get("phase") or "default"
                        )
                        trial_candidates = candidate_specs_for_hierarchy(
                            hierarchy, candidates
                        )
                        for candidate in trial_candidates:
                            if not candidate_applies_to_viewport(candidate, viewport):
                                continue
                            candidate_id = candidate_identity(candidate)
                            render_family = candidate_render_family(candidate)
                            phase_measurements: list[dict[str, Any]] = []
                            realized_by_phase: dict[str, dict[str, Any]] = {}
                            for scenario in scenarios:
                                responsive_scenario = responsive_phase_scenario(
                                    hierarchy,
                                    scenario,
                                    viewport,
                                    candidate,
                                )
                                phase = str(
                                    responsive_scenario.get("phase") or "default"
                                )
                                realized = realize_phase(
                                    hierarchy,
                                    candidate,
                                    responsive_scenario,
                                )
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
                                page.wait_for_timeout(60)
                                user_layout_calibration: dict[str, Any] = {}
                                if (
                                    isinstance(candidate, dict)
                                    and bool(
                                        (
                                            candidate.get("userLayoutMutation")
                                            or {}
                                        ).get("userTabWorkbench", False)
                                    )
                                ):
                                    user_layout_calibration = page.evaluate(
                                        USER_TAB_WORKBENCH_CALIBRATION_JS
                                    )
                                    page.wait_for_timeout(40)
                                measurement = page.evaluate(
                                    MEASURE_AND_OVERLAY_JS,
                                    {
                                        "hierarchyId": hierarchy["id"],
                                        "candidate": candidate_id,
                                        "renderFamily": render_family,
                                        "candidateMode": candidate_mode(candidate),
                                        "chrome": chrome,
                                        "viewportProfile": viewport.name,
                                        "viewportWidth": viewport.width,
                                        "viewportHeight": viewport.height,
                                        "responsiveProbe": viewport.responsive_probe,
                                        "phase": phase,
                                    },
                                )
                                measurement["viewportWidth"] = viewport.width
                                measurement["viewportHeight"] = viewport.height
                                measurement["responsiveProbe"] = bool(
                                    viewport.responsive_probe
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
                                    responsive_scenario
                                )
                                measurement["responsivePresentation"] = copy.deepcopy(
                                    realized.get("responsivePresentation") or {}
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
                                measurement["shadowOnly"] = candidate_shadow_only(candidate)
                                measurement["responsiveEligible"] = (
                                    candidate_responsive_eligible(candidate)
                                )
                                measurement["layoutHintCompilation"] = (
                                    copy.deepcopy(candidate.get("layoutHintCompilation") or {})
                                    if isinstance(candidate, dict)
                                    else {}
                                )
                                measurement["userLayoutMutation"] = (
                                    copy.deepcopy(candidate.get("userLayoutMutation") or {})
                                    if isinstance(candidate, dict)
                                    else {}
                                )
                                measurement["userLayoutCalibration"] = copy.deepcopy(
                                    user_layout_calibration
                                )
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
                                apply_phase_floor_gate_to_measurement(
                                    realized,
                                    measurement,
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
                            aggregate["shadowOnly"] = candidate_shadow_only(candidate)
                            aggregate["responsiveEligible"] = (
                                candidate_responsive_eligible(candidate)
                            )
                            aggregate["layoutHintCompilation"] = (
                                copy.deepcopy(candidate.get("layoutHintCompilation") or {})
                                if isinstance(candidate, dict)
                                else {}
                            )
                            aggregate["userLayoutMutation"] = (
                                copy.deepcopy(candidate.get("userLayoutMutation") or {})
                                if isinstance(candidate, dict)
                                else {}
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

    rendered_equivalence_groups = annotate_rendered_policy_equivalence(measurements)
    layout_hint_refinements = analyze_layout_hint_refinements(
        hierarchies=hierarchies,
        measurements=measurements,
    )
    responsive_policies = build_responsive_policies(
        hierarchies=hierarchies,
        measurements=measurements,
        profiles_by_hierarchy=profiles_by_hierarchy,
        responsive_mode=responsive_mode,
        hysteresis_px=responsive_hysteresis_px,
    )
    user_layout_hint_evidence = build_user_layout_hint_shadow_evidence(
        hierarchies
    )
    user_layout_hint_browser_evidence = analyze_user_layout_hint_browser_evidence(
        hierarchies=hierarchies,
        measurements=measurements,
        semantic_evidence=user_layout_hint_evidence,
        responsive_policies=responsive_policies,
    )
    best_by_hierarchy = best_by_hierarchy_viewport_rows(
        measurements,
        responsive_policies=responsive_policies,
    )
    pixel_rect_audit = pixel_rect_audit_summary(measurements)
    layout_hint_contracts = [
        compile_layout_hint_default(item)
        for item in hierarchies
        if item.get("layoutHintSource")
    ]

    return {
        "kind": "mcel.flog.synthetic.layout.trial.report",
        "generatedAt": generated_at,
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "paintedOwnershipMode": "exclusive-enforced",
        "paintedOwnershipEnforced": True,
        "phaseFloorGateMode": "unified-effective-share-absolute",
        "phaseFloorTolerance": PHASE_SHARE_FLOOR_TOLERANCE,
        "densityScoringMode": "phase-relative-active-presentation",
        "candidateRankingMode": "rendered-equivalence-deduplicated-margin-ranking",
        "layoutHintMode": LAYOUT_HINT_MODE,
        "layoutHintContractVersion": LAYOUT_HINT_CONTRACT_VERSION,
        "layoutHintContracts": layout_hint_contracts,
        "layoutHintRefinementVersion": LAYOUT_HINT_REFINEMENT_VERSION,
        "layoutHintRefinements": layout_hint_refinements,
        "layoutHintResponsiveVersion": LAYOUT_HINT_RESPONSIVE_VERSION,
        "userLayoutHintContractVersion": USER_LAYOUT_HINT_CONTRACT_VERSION,
        "userLayoutHintOperationVersion": USER_LAYOUT_HINT_OPERATION_VERSION,
        "userLayoutHintMode": USER_LAYOUT_HINT_MODE,
        "userLayoutHintEvidence": user_layout_hint_evidence,
        "userLayoutHintBrowserProofVersion": (
            USER_LAYOUT_HINT_BROWSER_PROOF_VERSION
        ),
        "userLayoutHintBrowserMode": USER_LAYOUT_HINT_BROWSER_MODE,
        "userLayoutHintBrowserEvidence": user_layout_hint_browser_evidence,
        "responsivePresentationContractVersion": (
            RESPONSIVE_PRESENTATION_CONTRACT_VERSION
        ),
        "responsiveCoverageVersion": RESPONSIVE_COVERAGE_VERSION,
        "responsiveTransitionProofVersion": RESPONSIVE_TRANSITION_PROOF_VERSION,
        "responsiveMode": responsive_mode,
        "responsivePolicyVersion": RESPONSIVE_POLICY_VERSION,
        "responsiveStateMachineVersion": RESPONSIVE_STATE_MACHINE_VERSION,
        "responsiveHysteresisPx": int(responsive_hysteresis_px),
        "responsivePolicies": responsive_policies,
        "renderedPolicyFingerprintVersion": RENDERED_POLICY_FINGERPRINT_VERSION,
        "renderedPolicyFingerprintBins": RENDERED_POLICY_FINGERPRINT_BINS,
        "renderedPolicyCoordinateSystem": AUTHORITATIVE_PIXEL_COORDINATE_SYSTEM,
        "pixelRectAuditVersion": PIXEL_RECT_AUDIT_VERSION,
        "pixelRectAudit": pixel_rect_audit,
        "renderedPolicyEquivalenceGroups": rendered_equivalence_groups,
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
                "layoutHintSourceDeclared": bool(item.get("layoutHintSource")),
                "layoutHintCompilation": (
                    compile_layout_hint_default(item)
                    if item.get("layoutHintSource")
                    else {}
                ),
                "responsiveContract": (
                    responsive_contract_for_hierarchy(item)
                    if (
                        responsive_mode == "all"
                        or (responsive_mode == "recursive" and item.get("layoutUnitTree"))
                    )
                    else {}
                ),
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
            {
                "name": vp.name,
                "width": vp.width,
                "height": vp.height,
                "responsiveProbe": bool(vp.responsive_probe),
            }
            for vp in all_viewports
        ],
        "baseViewports": [
            {"name": vp.name, "width": vp.width, "height": vp.height}
            for vp in viewports
        ],
        "responsiveViewports": [
            {
                "name": vp.name,
                "width": vp.width,
                "height": vp.height,
            }
            for vp in responsive_viewports
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


def rollup_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    evidence = measurement_margin_evidence(item)
    return (
        measurement_quality_rank(measurement_status(item)),
        -measurement_score(item),
        -float(evidence["worstPhaseHeadroom"]),
        -float(evidence["worstUnitScore"]),
        int(evidence["overlayPolicyCount"]),
        float(evidence["phaseScoreVariance"]),
        -float(evidence["effectiveFocusOccupancy"]),
        -int(evidence["compositionPreflightScore"]),
        str(item.get("candidate") or ""),
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
        if bool(measurement.get("renderedEquivalenceExcludedFromRanking")):
            continue
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



REPORT_DETAIL_COMPACT = "compact"
REPORT_DETAIL_FULL = "full"
COMPACT_MEASUREMENT_VERSION = "responsive-trial-summary-v1"


def compact_phase_summary(item: dict[str, Any]) -> list[dict[str, Any]]:
    phase_fit = item.get("phaseFit") or {}
    phases = phase_fit.get("phases") or []
    if phases:
        return [
            {
                "phase": str(phase.get("phase") or "default"),
                "score": phase.get("score", 0),
                "rawScore": phase.get("rawScore", phase.get("score", 0)),
                "dominantShare": phase.get("dominantShare", 0),
                "minDominantShare": phase.get("minDominantShare", 0),
                "dominantFloorMet": bool(phase.get("dominantFloorMet", False)),
                "dominantHeadroom": phase.get("dominantHeadroom", 0),
                "dominantRawHeadroom": phase.get(
                    "dominantRawHeadroom", phase.get("dominantHeadroom", 0)
                ),
                "hardFailureCount": int(phase.get("hardFailureCount", 0) or 0),
            }
            for phase in phases
        ]

    summaries: list[dict[str, Any]] = []
    for phase in item.get("phaseMeasurements") or []:
        facts = phase.get("geometryFacts") or {}
        classification = phase.get("classification") or {}
        floor = classification.get("phaseFloorGate") or {}
        summaries.append(
            {
                "phase": str(phase.get("phase") or "default"),
                "score": classification.get("score", 0),
                "rawScore": classification.get(
                    "selectionScoreRaw", classification.get("score", 0)
                ),
                "dominantShare": facts.get(
                    "effectiveFocusShare", facts.get("focusShare", 0)
                ),
                "minDominantShare": floor.get(
                    "minimumShare",
                    facts.get("minimumFocusShare", facts.get("minFocusShare", 0)),
                ),
                "dominantFloorMet": bool(
                    floor.get("met", classification.get("status") != "fail")
                ),
                "dominantHeadroom": floor.get("rankedHeadroom", 0),
                "dominantRawHeadroom": floor.get(
                    "rawHeadroom", floor.get("rankedHeadroom", 0)
                ),
                "hardFailureCount": int(
                    classification.get("phaseFloorFailureCount", 0) or 0
                ),
            }
        )
    return summaries


def compact_measurement_summary(item: dict[str, Any]) -> dict[str, Any]:
    classification = item.get("classification") or {}
    facts = item.get("geometryFacts") or {}
    phase_fit = item.get("phaseFit") or {}
    unit_fit = item.get("layoutUnitFit") or {}
    composition = item.get("unitComposition") or {}
    painted = facts.get("paintedOwnership") or facts.get("paintedOwnershipShadow") or {}
    margin = measurement_margin_evidence(item)
    return {
        "hierarchyId": str(item.get("hierarchyId") or ""),
        "viewportProfile": str(item.get("viewportProfile") or ""),
        "viewportWidth": int(item.get("viewportWidth", 0) or 0),
        "viewportHeight": int(item.get("viewportHeight", 0) or 0),
        "responsiveProbe": bool(item.get("responsiveProbe", False)),
        "candidate": str(item.get("candidate") or ""),
        "candidateMode": str(item.get("candidateMode") or "legacy-family"),
        "shadowOnly": bool(item.get("shadowOnly", False)),
        "responsiveEligible": bool(item.get("responsiveEligible", False)),
        "responsivePresentation": responsive_presentation_evidence(item),
        "layoutHintCompilation": copy.deepcopy(
            item.get("layoutHintCompilation") or {}
        ),
        "layoutHintOutcome": copy.deepcopy(item.get("layoutHintOutcome") or {}),
        "renderFamily": str(item.get("renderFamily") or item.get("candidate") or ""),
        "canonicalPhase": str(item.get("canonicalPhase") or item.get("phase") or ""),
        "status": str(classification.get("status") or "fail"),
        "score": classification.get("score", 0),
        "selectionScoreRaw": classification.get(
            "selectionScoreRaw",
            classification.get("selectionScore", classification.get("score", 0)),
        ),
        "geometryScore": classification.get(
            "geometryScore", classification.get("score", 0)
        ),
        "contractFitScore": classification.get("contractFitScore", 0),
        "affordanceFitScore": classification.get("affordanceFitScore", 0),
        "phaseFitScore": classification.get("phaseFitScore", 0),
        "layoutUnitFitScore": classification.get("layoutUnitFitScore", 100),
        "phaseFloorFailureCount": int(
            phase_fit.get(
                "phaseFloorFailureCount",
                classification.get("phaseFloorFailureCount", 0),
            )
            or 0
        ),
        "phaseHardFailureCount": int(phase_fit.get("hardFailureCount", 0) or 0),
        "layoutUnitHardFailureCount": int(unit_fit.get("hardFailureCount", 0) or 0),
        "worstPhaseHeadroom": margin.get("worstPhaseHeadroom", -1),
        "worstUnitScore": margin.get("worstUnitScore", 100),
        "phaseScoreVariance": margin.get("phaseScoreVariance", 0),
        "effectiveFocusOccupancy": margin.get("effectiveFocusOccupancy", 0),
        "overlayPolicyCount": margin.get("overlayPolicyCount", 0),
        "focusShare": facts.get("focusShare", 0),
        "effectiveFocusShare": facts.get(
            "effectiveFocusShare",
            painted.get("effectiveFocusShare", facts.get("focusShare", 0)),
        ),
        "desiredFocusShare": facts.get("desiredFocusShare", 0),
        "activePresentationUnclaimedRatio": facts.get(
            "activePresentationUnclaimedRatio",
            facts.get("unclaimedAreaRatio", 0),
        ),
        "intentionalInactiveRootRatio": facts.get("intentionalInactiveRootRatio", 0),
        "blockedCriticalControlCount": int(
            facts.get("blockedCriticalControlCount", 0) or 0
        ),
        "undeclaredPartitionOverlapShare": facts.get(
            "undeclaredPartitionOverlapShare",
            painted.get("undeclaredPartitionOverlapShare", 0),
        ),
        "overlayBudgetExceededCount": len(
            painted.get("overlayBudgetExceeded") or []
        ),
        "responsivePolicySelected": bool(item.get("responsivePolicySelected", False)),
        "responsiveCapacityBand": str(item.get("responsiveCapacityBand") or ""),
        "responsiveCapacityAdmissible": item.get("responsiveCapacityAdmissible"),
        "responsiveRemediation": copy.deepcopy(item.get("responsiveRemediation") or {}),
        "unitPolicies": copy.deepcopy(composition.get("unitPolicies") or {}),
        "compositionPreflightScore": composition.get("preflightScore"),
        "renderedPolicyFingerprint": str(
            item.get("renderedPolicyFingerprint") or ""
        ),
        "renderedEquivalenceRepresentative": str(
            item.get("renderedEquivalenceRepresentative")
            or item.get("candidate")
            or ""
        ),
        "phases": compact_phase_summary(item),
        "failureReasons": list(classification.get("failureReasons") or [])[:8],
        "warnings": list(classification.get("warnings") or [])[:4],
        "snapshots": copy.deepcopy(item.get("snapshots") or {}),
        "phaseSnapshots": copy.deepcopy(item.get("phaseSnapshots") or {}),
    }


def compact_report_payload(report: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: copy.deepcopy(value)
        for key, value in report.items()
        if key != "measurements"
    }
    measurements = report.get("measurements") or []
    payload["reportDetail"] = REPORT_DETAIL_COMPACT
    payload["measurementRecordMode"] = COMPACT_MEASUREMENT_VERSION
    payload["fullDiagnosticMeasurementsIncluded"] = False
    payload["fullDiagnosticMeasurementCount"] = len(measurements)
    payload["measurements"] = [
        compact_measurement_summary(item) for item in measurements
    ]
    return payload


def report_payload_for_detail(
    report: dict[str, Any],
    report_detail: str,
) -> dict[str, Any]:
    detail = str(report_detail or REPORT_DETAIL_COMPACT).lower()
    if detail == REPORT_DETAIL_FULL:
        payload = copy.deepcopy(report)
        payload["reportDetail"] = REPORT_DETAIL_FULL
        payload["measurementRecordMode"] = "full-browser-diagnostics"
        payload["fullDiagnosticMeasurementsIncluded"] = True
        return payload
    if detail != REPORT_DETAIL_COMPACT:
        raise ValueError("report_detail must be one of: compact, full")
    return compact_report_payload(report)


def append_compact_measurement_index(
    lines: list[str],
    measurements: list[dict[str, Any]],
) -> None:
    lines.append("## Compact trial measurement index")
    lines.append("")
    lines.append(
        "The default report keeps one concise record per trial. "
        "Use `--report-detail full` only for a targeted diagnostic run."
    )
    lines.append("")
    for item in measurements:
        summary = compact_measurement_summary(item)
        lines.append(
            f"- `{summary['hierarchyId']}` / `{summary['viewportProfile']}` / "
            f"`{summary['candidate']}`: status=`{summary['status']}` "
            f"score=`{summary['score']}` "
            f"headroom=`{float(summary['worstPhaseHeadroom']):+.4f}` "
            f"blocked=`{summary['blockedCriticalControlCount']}` "
            f"partitionOverlap=`{float(summary['undeclaredPartitionOverlapShare']):.4f}`"
        )
        if summary["failureReasons"]:
            lines.append(
                f"  - First failure: {summary['failureReasons'][0]}"
            )
    lines.append("")


def write_reports(
    report: dict[str, Any],
    output_dir: Path,
    *,
    report_detail: str = REPORT_DETAIL_FULL,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "layout-snapshot-report.json"
    md_path = output_dir / "layout-snapshot-report.md"
    payload = report_payload_for_detail(report, report_detail)
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append("# FLOG Synthetic Layout Trial Report")
    lines.append("")
    lines.append(f"- Generated: `{report['generatedAt']}`")
    lines.append(f"- Report detail: `{str(report_detail or REPORT_DETAIL_COMPACT).lower()}`")
    lines.append(f"- Smoke level: `{report['smokeLevel']}`")
    lines.append(f"- Geometry engine: `{report['geometryEngine']}`")
    lines.append(
        f"- Painted ownership: `{report.get('paintedOwnershipMode', 'unavailable')}` "
        f"(enforced=`{bool(report.get('paintedOwnershipEnforced', False))}`)"
    )
    lines.append(
        f"- Phase-floor gate: `{report.get('phaseFloorGateMode', 'legacy-ratio')}` "
        f"tolerance=`{float(report.get('phaseFloorTolerance', PHASE_SHARE_FLOOR_TOLERANCE)):.4f}`"
    )
    lines.append(
        f"- Density scoring: `{report.get('densityScoringMode', 'root-wide')}`"
    )
    lines.append(
        f"- Candidate ranking: `{report.get('candidateRankingMode', 'integer-score-only')}`"
    )
    lines.append(
        f"- Layout-hint compiler: "
        f"`{report.get('layoutHintContractVersion', 'not-declared')}` "
        f"mode=`{report.get('layoutHintMode', 'off')}`"
    )
    lines.append(
        f"- Layout-hint refinement: "
        f"`{report.get('layoutHintRefinementVersion', 'off')}` "
        "(shadow evidence only)"
    )
    lines.append(
        f"- Responsive hint compiler: "
        f"`{report.get('layoutHintResponsiveVersion', 'off')}` "
        f"presentation=`{report.get('responsivePresentationContractVersion', 'off')}` "
        "(FLOG-only)"
    )
    lines.append(
        f"- User layout hints: "
        f"`{report.get('userLayoutHintContractVersion', 'off')}` "
        f"operations=`{report.get('userLayoutHintOperationVersion', 'off')}` "
        f"mode=`{report.get('userLayoutHintMode', 'off')}`"
    )
    lines.append(
        f"- User layout browser proof: "
        f"`{report.get('userLayoutHintBrowserProofVersion', 'off')}` "
        f"mode=`{report.get('userLayoutHintBrowserMode', 'off')}`"
    )
    lines.append(
        f"- Responsive resize policy: "
        f"`{report.get('responsivePolicyVersion', 'off')}` "
        f"mode=`{report.get('responsiveMode', 'off')}` "
        f"hysteresis=`{int(report.get('responsiveHysteresisPx', 0) or 0)}px`"
    )
    lines.append(
        f"- Responsive transition proof: "
        f"`{report.get('responsiveTransitionProofVersion', 'off')}`"
    )
    lines.append(
        f"- Rendered-policy fingerprint: "
        f"`{report.get('renderedPolicyFingerprintVersion', 'unavailable')}` "
        f"bins=`{report.get('renderedPolicyFingerprintBins', 0)}`"
    )
    pixel_rect_audit = report.get("pixelRectAudit") or {}
    lines.append(
        f"- Pixel-rect coordinate audit: "
        f"`{report.get('pixelRectAuditVersion', 'unavailable')}` "
        f"coordinateSystem=`{report.get('renderedPolicyCoordinateSystem', 'unknown')}` "
        f"ok=`{pixel_rect_audit.get('okCount', 0)}/{pixel_rect_audit.get('measurementCount', 0)}` "
        f"cssFallbackRecords=`{pixel_rect_audit.get('cssFallbackRecordCount', 0)}`"
    )
    lines.append(
        f"- Rendered-equivalence groups removed from ranking: "
        f"`{len(report.get('renderedPolicyEquivalenceGroups') or [])}`"
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
    lines.append("Stage B enforces exclusive painted ownership, effective focus/companion shares, partition isolation, overlay budgets, and foreign-owner critical-control interception from Chromium hit testing.")
    lines.append("Stage C scores density inside the active focus layout unit (or a semantic presentation envelope when no recursive unit exists), reports intentional inactive root space separately, and resolves integer-score ties with phase margin evidence.")
    lines.append("Stage D fingerprints cross-phase painted geometry from authoritative device-pixel rectangles, without policy names, removes rendered-equivalent recursive aliases before ranking, and reports which declared local policies collapsed to the same browser realization.")
    lines.append("Stage E applies one absolute effective-share phase-floor gate in browser classification and aggregate phase scoring, with a shared subpixel tolerance; any real floor miss remains a hard failure.")
    lines.append("Stage F ranks a responsive policy across capacity bands, permits stronger remediation only when the viewport contract allows it, requires remediation to be monotonic as space shrinks, and derives separate shrink/grow thresholds to prevent resize oscillation.")
    lines.append("Milestone 1E compares the deterministic authored hint result with already-rendered one-policy alternatives, reports the smallest browser-verified hint correction, and prepares the authored fallback chain without editing live application files.")
    lines.append("Milestone 2 compiles distinct right, bottom, tab, sequential-stage, and trigger realizations; transforms each phase into a capacity-relative presentation contract; derives capacity bands from authored minimum geometry; samples both sides of every derived boundary; and treats invalid sampled intervals as uncovered rather than ranking through them.")
    lines.append("Milestone 2.4 samples all adjacent authored transitions, distinguishes tolerance-only passing from positive raw headroom, and certifies hysteresis only when the browser proves the full declared overlap width.")
    lines.append("Milestone 2.5 resolves those verified envelopes as one ordered hysteretic state machine; transition probes remain evidence and cannot act as local policy winners.")
    lines.append("Milestone 2.6 hardens the narrow tab realization by trimming decorative tab and feedback chrome while preserving semantic surfaces, phase floors, clipping gates, and transition thresholds.")
    lines.append("Milestone 3.1 models live user layout intent as semantic dock-tree operations, validates those operations against authored capabilities and invariants, and proves responsive remediation/restoration without editing live application files.")
    lines.append("Milestone 3.2 compiles accepted user preferences into bounded shadow candidates and measures their personalized geometry across responsive capacities. Milestone 3.3 distinguishes user-authored tab workbenches from emergency responsive tabs and requires every claimed restoration to reproduce the same painted-geometry fingerprint. Milestone 3.4 exposed the parent-coordinate mismatch. Milestone 3.5 measured the realized parent and resolved root-relative requirements into local shares. Milestone 3.6 assigns exact summary tracks, stretches each semantic summary to its owned slot, and verifies both slot allocation and painted delivery.")
    lines.append("Raw rectangles remain in the report for diagnosis, but ranking, pass/fail, phase minimums, contract visibility, and recursive unit scoring use effective painted geometry.")
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
        responsive_contract = item.get("responsiveContract") or {}
        if responsive_contract:
            band_text = "; ".join(
                f"{band.get('id')}≥{band.get('minWidth')}px:"
                f"level≤{band.get('maxRemediationLevel')}"
                for band in responsive_contract.get("bands") or []
            )
            lines.append(f"- Responsive capacity bands: `{band_text}`")
        lines.append("")

    if report.get("layoutHintContracts"):
        lines.append("## Shadow layout-hint compilation")
        lines.append("")
        lines.append(
            "Milestone 1 compiles HTML-shaped `data-mc-layout-*` hints inside "
            "FLOG only. The resulting candidate is rendered as evidence but is "
            "excluded from ranking, responsive selection, and all live application "
            "files."
        )
        lines.append("")
        for compilation in report.get("layoutHintContracts") or []:
            lines.append(
                f"### `{compilation.get('hierarchyId', '')}` — "
                f"state=`{compilation.get('state', 'unknown')}`"
            )
            lines.append("")
            lines.append(
                f"- Contract version: `{compilation.get('version', '')}` "
                f"mode=`{compilation.get('mode', '')}` "
                f"capacity=`{compilation.get('capacity', '')}`"
            )
            lines.append(
                f"- Live application files touched: "
                f"`{bool(compilation.get('liveApplicationFilesTouched', False))}`"
            )
            dock_tree = compilation.get("dockTree") or {}
            if dock_tree:
                lines.append(
                    f"- Dock root: `{dock_tree.get('id', '')}` "
                    f"model=`{dock_tree.get('model', '')}` "
                    f"policy=`{dock_tree.get('policy', '')}`"
                )
                for zone in dock_tree.get("zones") or []:
                    lines.append(
                        f"  - `{zone.get('id', '')}`: "
                        f"`{', '.join(zone.get('units') or [])}`"
                    )
            candidate = compilation.get("candidate") or {}
            if candidate:
                lines.append(
                    f"- Shadow candidate: `{candidate.get('id', '')}` "
                    "(excluded from ranking)"
                )
            if compilation.get("issues"):
                lines.append("- Issues:")
                for issue in compilation.get("issues") or []:
                    lines.append(f"  - {issue}")
            else:
                lines.append("- Issues: `none`")
            lines.append(
                f"- Future HTML annotation targets: "
                f"`{len(compilation.get('annotationRecommendations') or [])}`"
            )
            lines.append("")

    if report.get("layoutHintRefinements"):
        lines.append("## Shadow layout-hint refinement")
        lines.append("")
        lines.append(
            "Milestone 1E uses existing Chromium trials to find the smallest "
            "one-unit policy change that improves the authored default, and to "
            "prepare the declared fallback chain. Recommendations remain "
            "machine-readable evidence; they are never applied to live HTML."
        )
        lines.append("")
        for refinement in report.get("layoutHintRefinements") or []:
            lines.append(
                f"### `{refinement.get('hierarchyId', '')}` — "
                f"state=`{refinement.get('state', 'unknown')}`"
            )
            lines.append("")
            lines.append(
                f"- Reference viewport: "
                f"`{refinement.get('referenceViewportProfile', '')}`"
            )
            lines.append(
                f"- Required robust headroom: "
                f"`{float(refinement.get('robustHeadroom', 0) or 0):.2%}`"
            )
            lines.append(
                f"- Live application files touched: "
                f"`{bool(refinement.get('liveApplicationFilesTouched', False))}`"
            )
            authored = refinement.get("authoredEvidenceByViewport") or {}
            for profile, evidence in authored.items():
                lines.append(
                    f"- Authored `{profile}`: "
                    f"outcome=`{evidence.get('outcome', 'unknown')}` "
                    f"headroom=`{float(evidence.get('worstPhaseHeadroom', -1) or 0):+.3%}`"
                )
            revisions = refinement.get("recommendedContractRevisions") or []
            if revisions:
                lines.append("- Recommended minimal contract revision(s):")
                for revision in revisions:
                    lines.append(
                        f"  - `{revision.get('unitId', '')}`: "
                        f"`{revision.get('currentPolicy', '')}` → "
                        f"`{revision.get('suggestedPolicy', '')}`; "
                        f"headroom "
                        f"`{float(revision.get('currentHeadroom', 0) or 0):+.3%}` → "
                        f"`{float(revision.get('suggestedHeadroom', 0) or 0):+.3%}`"
                    )
            else:
                lines.append("- Recommended minimal contract revisions: `none`")
            fallback = refinement.get("fallbackPreparation") or {}
            if fallback:
                lines.append(
                    f"- Fallback unit: `{fallback.get('unitId', '')}` "
                    f"preferred=`{fallback.get('preferredPlacement', '')}`"
                )
                for entry in fallback.get("chain") or []:
                    lines.append(
                        f"  - `{entry.get('placement', '')}` → "
                        f"`{entry.get('policy', '') or 'unmapped'}` "
                        f"state=`{entry.get('state', '')}`"
                    )
                    for evidence in entry.get("measurements") or []:
                        lines.append(
                            f"    - `{evidence.get('viewportProfile', '')}`: "
                            f"outcome=`{evidence.get('outcome', 'unknown')}` "
                            f"candidate=`{evidence.get('candidate', '')}` "
                            f"headroom=`{float(evidence.get('worstPhaseHeadroom', -1) or 0):+.3%}`"
                        )
            lines.append("")

    if report.get("userLayoutHintEvidence"):
        lines.append("## Shadow user layout hints")
        lines.append("")
        lines.append(
            "Milestone 3.1 applies semantic user operations to the authored dock "
            "tree in FLOG only. Preferences are retained through responsive "
            "fallback and restored when capacity returns; no raw pixel coordinates "
            "or live application files are involved."
        )
        lines.append("")
        for evidence in report.get("userLayoutHintEvidence") or []:
            lines.append(
                f"### `{evidence.get('hierarchyId', '')}` — "
                f"state=`{evidence.get('state', 'unknown')}`"
            )
            lines.append("")
            lines.append(
                f"- Contract: `{evidence.get('version', '')}` "
                f"operations=`{evidence.get('operationVersion', '')}` "
                f"mode=`{evidence.get('mode', '')}`"
            )
            lines.append(
                f"- Live application files touched: "
                f"`{bool(evidence.get('liveApplicationFilesTouched', False))}`"
            )
            lines.append(
                f"- Storage model: `{evidence.get('storageModel', '')}` "
                f"rawPixelCoordinatesStored="
                f"`{bool(evidence.get('rawPixelCoordinatesStored', False))}`"
            )
            for item in evidence.get("operationTrace") or []:
                lines.append(
                    f"  - `{item.get('operationId', '')}` "
                    f"kind=`{item.get('kind', '')}` "
                    f"outcome=`{item.get('outcome', '')}` "
                    f"userId=`{item.get('userId', '')}`"
                )
            round_trip = evidence.get("roundTrip") or {}
            lines.append(
                f"- Responsive preference round trip: "
                f"restored=`{bool(round_trip.get('restoredAfterRoundTrip', False))}` "
                f"retained=`{bool(round_trip.get('preferenceRetainedAtEveryProbe', False))}`"
            )
            for probe in round_trip.get("probes") or []:
                placements = ", ".join(
                    f"{unit}={placement}"
                    for unit, placement in (
                        probe.get("effectivePlacements") or {}
                    ).items()
                )
                lines.append(
                    f"  - `{probe.get('viewportProfile', '')}` "
                    f"{probe.get('width', 0)}px band=`{probe.get('capacityBand', '')}` "
                    f"placements=`{placements}` "
                    f"remediations=`{len(probe.get('remediations') or [])}`"
                )
            rejected = evidence.get("rejectedInvariantExample") or {}
            lines.append(
                f"- Required-workflow collapse rejected: "
                f"`{bool(rejected.get('rejected', False))}`"
            )
            lines.append("")

    if report.get("userLayoutHintBrowserEvidence"):
        lines.append("## Shadow user-layout browser proofs")
        lines.append("")
        lines.append(
            "Milestone 3.6 compiles accepted semantic preferences into actual "
            "FLOG candidates. Chromium applies the same clipping, ownership, "
            "interception, phase-floor, and responsive-presentation gates used by "
            "the authored layouts. These proofs remain synthetic and do not edit "
            "live application files."
        )
        lines.append("")
        for evidence in report.get("userLayoutHintBrowserEvidence") or []:
            lines.append(
                f"### `{evidence.get('hierarchyId', '')}` — "
                f"state=`{evidence.get('state', 'unknown')}`"
            )
            lines.append("")
            lines.append(
                f"- Contract: `{evidence.get('version', '')}` "
                f"mode=`{evidence.get('mode', '')}`"
            )
            lines.append(
                f"- Profiles: `{evidence.get('profileCount', 0)}` "
                f"browser aggregates=`{evidence.get('browserAggregateCount', 0)}` "
                f"PNG proofs=`{evidence.get('pngProofCount', 0)}`"
            )
            lines.append(
                f"- Browser gates passed: "
                f"`{bool(evidence.get('allBrowserGatesPassed', False))}` "
                f"preferences retained=`{bool(evidence.get('allPreferencesRetained', False))}` "
                f"hysteresis coverage=`{bool(evidence.get('allHysteresisCoveragePassed', False))}` "
                f"restorations matched=`{bool(evidence.get('allRequiredRestorationsMatched', False))}`"
            )
            lines.append(
                f"- Live application files touched: "
                f"`{bool(evidence.get('liveApplicationFilesTouched', False))}`"
            )
            for profile in evidence.get("profiles") or []:
                lines.append(
                    f"  - Profile `{profile.get('profileId', '')}` "
                    f"state=`{profile.get('state', 'unknown')}` "
                    f"aggregates=`{profile.get('browserAggregateCount', 0)}` "
                    f"PNGs=`{profile.get('pngProofCount', 0)}` "
                    f"path=`{' → '.join(profile.get('placementSequence') or [])}` "
                    f"hysteresis=`{bool(profile.get('hysteresisCoveragePassed', False))}` "
                    f"restoration=`{profile.get('restorationFingerprintStatus', 'not-applicable')}`"
                )
                for proof in profile.get("proofs") or []:
                    lines.append(
                        f"    - `{proof.get('viewportProfile', '')}` "
                        f"{proof.get('width', 0)}×{proof.get('height', 0)} "
                        f"variant=`{proof.get('variant', '')}` "
                        f"placement=`{proof.get('effectivePlacement', '')}` "
                        f"share=`{float(proof.get('preferredShare', 0) or 0):.1%}` "
                        f"status=`{proof.get('status', '')}` "
                        f"headroom=`{float(proof.get('worstPhaseHeadroom', -1) or 0):+.3%}` "
                        f"blocked=`{proof.get('blockedCriticalControlCount', 0)}` "
                        f"summaryAllocation=`{'pass' if proof.get('summaryAllocationPassed', True) else 'fail'}` "
                        f"remediations=`{len(proof.get('remediations') or [])}`"
                    )
                    diagnostics = proof.get("summaryAllocationDiagnostics") or {}
                    for calibration in diagnostics.get("calibrations") or []:
                        delivered = dict(
                            calibration.get("deliveredRootShares") or {}
                        )
                        local = dict(
                            calibration.get("resolvedLocalShares") or {}
                        )
                        lines.append(
                            f"      - Summary calibration `{calibration.get('phase', '')}`: "
                            f"parent="
                            f"{float(calibration.get('initialParentRootShare', 0) or 0):.2%}"
                            f"→{float(calibration.get('finalParentRootShare', 0) or 0):.2%}; "
                            f"workflow local={float(local.get('workflow', 0) or 0):.2%} "
                            f"delivered={float(delivered.get('workflow', 0) or 0):.2%}"
                        )
                    for failure in diagnostics.get("failures") or []:
                        lines.append(f"      - Summary allocation: {failure}")
            undo_reset = evidence.get("undoResetEvidence") or {}
            lines.append(
                f"- Undo/reset restored authored default: "
                f"`{bool(undo_reset.get('restoredAuthoredDefault', False))}`"
            )
            migration = evidence.get("migrationEvidence") or {}
            lines.append(
                f"- Stable-ID migration: state=`{migration.get('state', '')}` "
                f"changes=`{len(migration.get('changes') or [])}`"
            )
            lines.append("")

    if report.get("responsivePolicies"):
        lines.append("## Responsive resize policies")
        lines.append("")
        lines.append(
            "Selections are optimized as one authored wide-to-compact policy rather than "
            "as unrelated per-viewport winners. A stronger remediation becomes "
            "admissible only after lower levels fail or lose robust headroom."
        )
        lines.append("")
        for policy in report.get("responsivePolicies") or []:
            lines.append(
                f"### `{policy.get('hierarchyId', '')}` — "
                f"state=`{policy.get('state', 'unknown')}`"
            )
            lines.append("")
            lines.append(
                f"- Semantic contract stable: "
                f"`{bool(policy.get('semanticContractStable', False))}`"
            )
            lines.append(
                f"- Resize stability: wide→narrow=`{bool(policy.get('wideToNarrowStable', False))}` "
                f"narrow→wide=`{bool(policy.get('narrowToWideStable', False))}`"
            )
            lines.append(
                f"- Worst viewport/phase headroom: "
                f"`{float(policy.get('worstViewportPhaseHeadroom', -1) or 0):+.4f}`"
            )
            lines.append(
                f"- Switches: `{policy.get('switchCount', 0)}` "
                f"gaps=`{policy.get('transitionGapCount', 0)}` "
                f"unverifiedTransitions=`{policy.get('unverifiedTransitionCount', 0)}` "
                f"shortHysteresis=`{policy.get('insufficientHysteresisTransitionCount', 0)}` "
                f"forcedBeyondBand=`{policy.get('forcedBeyondBandCount', 0)}` "
                f"unnecessaryRemediation=`{policy.get('unnecessaryRemediationCount', 0)}` "
                f"monotonicViolations=`{policy.get('monotonicViolationCount', 0)}`"
            )
            lines.append(
                f"- Probe coverage: count=`{policy.get('probeCount', 0)}` "
                f"maxGap=`{policy.get('maxProbeGapPx', 0)}px` "
                f"complete=`{bool(policy.get('coverageComplete', False))}` "
                f"uncoveredIntervals=`{policy.get('uncoveredIntervalCount', 0)}`"
            )
            lines.append(
                f"- Stateful policy probes: `{policy.get('policyProbeCount', policy.get('probeCount', 0))}` "
                f"transition evidence probes: `{policy.get('transitionEvidenceCount', 0)}` "
                f"stateMachine=`{policy.get('stateMachineVersion', '')}`"
            )
            stateful_path = policy.get("statefulPath") or {}
            if stateful_path:
                lines.append(
                    f"- Stateful candidate sequence: "
                    f"`{' → '.join(stateful_path.get('candidateSequence') or []) or 'none'}` "
                    f"state=`{stateful_path.get('state', 'unknown')}`"
                )
            for selection in policy.get("selections") or []:
                lines.append(
                    f"  - `{selection.get('viewportProfile')}` "
                    f"{selection.get('width')}×{selection.get('height')} "
                    f"band=`{selection.get('band')}` "
                    f"candidate=`{selection.get('candidate')}` "
                    f"remediation=`{selection.get('remediationLevel')}:"
                    f"{selection.get('remediationLabel')}` "
                    f"headroom=`{float(selection.get('worstPhaseHeadroom', 0) or 0):+.4f}` "
                    f"status=`{selection.get('status')}` "
                    f"admissibility=`{selection.get('capacityAdmissibilityState', 'unknown')}` "
                    f"source=`{selection.get('admissibilitySource', '')}` "
                    f"presentations=`{', '.join((selection.get('responsivePresentation') or {}).get('modes') or [])}`"
                )
            for transition in policy.get("transitions") or []:
                lines.append(
                    f"  - Transition `{transition.get('fromCandidate')}` → "
                    f"`{transition.get('toCandidate')}`: shrink below "
                    f"`{transition.get('switchDownBelow')}px`, grow above "
                    f"`{transition.get('switchUpAbove')}px` "
                    f"(hysteresis `{transition.get('hysteresisPx')}px`/"
                    f"`{transition.get('requiredHysteresisPx', 0)}px` required, "
                    f"state=`{transition.get('transitionState', 'unknown')}`, "
                    f"positiveOverlap=`{transition.get('positiveOverlapMinWidth', 0)}–"
                    f"{transition.get('positiveOverlapMaxWidth', 0)}px`, "
                    f"requirementMet=`{bool(transition.get('hysteresisRequirementMet', False))}`)"
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

    equivalence_groups = report.get("renderedPolicyEquivalenceGroups") or []
    if equivalence_groups:
        lines.append("## Rendered-policy equivalence groups")
        lines.append("")
        lines.append(
            "Only the representative of each cross-phase painted-geometry fingerprint "
            "participates in ranking. PNG trials and raw measurements remain available "
            "for every declared candidate."
        )
        lines.append("")
        for group in equivalence_groups:
            lines.append(
                f"- `{group.get('hierarchyId')}` / `{group.get('viewportProfile')}`: "
                f"representative=`{group.get('representative')}` "
                f"groupSize=`{group.get('groupSize', 0)}` "
                f"fingerprint=`{str(group.get('fingerprint') or '')[:16]}…`"
            )
            aliases = group.get("equivalentAliases") or []
            if aliases:
                lines.append(
                    f"  - Equivalent aliases excluded from ranking: "
                    f"`{', '.join(aliases)}`"
                )
            for diagnostic in group.get("policyAliasDiagnostics") or []:
                lines.append(
                    f"  - Policy realization alias in `{diagnostic.get('unitId')}`: "
                    f"`{', '.join(diagnostic.get('policies') or [])}`"
                )
        lines.append("")

    lines.append("## Best candidate by hierarchy and viewport")
    lines.append("")
    lines.append(
        "`selectionState=bestPassingCandidate` means the row is the best fixed-viewport "
        "`pass`/`watch` trial. `selectionState=responsivePolicySelection` means the row "
        "was chosen as part of the stable cross-viewport policy. "
        "`selectionState=noPassingCandidate` or `responsiveTransitionGap` means no "
        "acceptable realization covered that viewport."
    )
    lines.append("Rendered-equivalent recursive candidates are deduplicated first. Equal integer scores among distinct realizations are then resolved by worst phase headroom, worst recursive-unit score, fewer overlay policies, lower phase-score variance, higher effective focus occupancy, then higher composition preflight score.")
    lines.append("")
    for item in report["bestByHierarchyViewport"]:
        selection_state = item.get("selectionState", "bestPassingCandidate")
        no_passing = bool(item.get("noPassingCandidate"))
        lines.append(f"- `{item['hierarchyId']}` / `{item['viewportProfile']}`: `{item['candidate']}` score=`{item['score']}` rawScore=`{item.get('selectionScoreRaw', item['score']):.4f}` status=`{item['status']}` selectionState=`{selection_state}` activeUnclaimed=`{item['unclaimedAreaRatio']:.4f}` rootUnclaimed=`{item.get('rootUnclaimedAreaRatio', item['unclaimedAreaRatio']):.4f}` intentionalInactiveRoot=`{item.get('intentionalInactiveRootRatio', 0):.4f}` focus=`{item['focusShare']:.4f}` target=`{item['desiredFocusShare']:.2f}` occupancy=`{item.get('usefulFocusOccupancy', 0):.4f}` proximity=`{item.get('companionProximityScore', 1):.4f}` contractFit=`{item.get('contractFitScore', 0)}` affordanceFit=`{item.get('affordanceFitScore', 0)}` phaseFit=`{item.get('phaseFitScore', 0)}` unitFit=`{item.get('layoutUnitFitScore', 100)}` contractState=`{item.get('contractFitState', 'notEvaluated')}` affordanceState=`{item.get('affordanceFitState', 'notEvaluated')}` phaseState=`{item.get('phaseFitState', 'notEvaluated')}` unitState=`{item.get('layoutUnitFitState', 'notDeclared')}`")
        margin = item.get("selectionMarginEvidence") or {}
        lines.append(
            "  - Margin-aware tie-break: "
            f"worstPhaseHeadroom=`{float(margin.get('worstPhaseHeadroom', -1)):+.4f}` "
            f"rawWorstPhaseHeadroom=`{float(item.get('phaseWorstRawDominantHeadroom', margin.get('worstPhaseHeadroom', -1))):+.4f}` "
            f"floorFailures=`{int(item.get('phaseFloorFailureCount', 0))}` "
            f"floorTolerance=`{float(item.get('phaseFloorTolerance', PHASE_SHARE_FLOOR_TOLERANCE)):.4f}` "
            f"worstUnit=`{float(margin.get('worstUnitScore', 0)):.4f}` "
            f"overlays=`{int(margin.get('overlayPolicyCount', 0))}` "
            f"phaseVariance=`{float(margin.get('phaseScoreVariance', 0)):.4f}` "
            f"effectiveFocusOccupancy=`{float(margin.get('effectiveFocusOccupancy', 0)):.4f}` "
            f"preflight=`{int(margin.get('compositionPreflightScore', -1))}`"
        )
        if item.get("renderedEquivalentAliases"):
            lines.append(
                "  - Rendered-equivalent aliases excluded from ranking: "
                f"`{', '.join(item.get('renderedEquivalentAliases') or [])}`"
            )
        if item.get("paintedOwnershipEnforced"):
            lines.append(
                "  - Painted ownership (enforced): "
                f"rawFocus=`{item.get('rawFocusShare', item.get('focusShare', 0)):.4f}` "
                f"effectiveFocus=`{item.get('effectiveFocusShare', item.get('focusShare', 0)):.4f}` "
                f"focusOccluded=`{item.get('focusOccludedShare', 0):.4f}` "
                f"exclusiveOwned=`{item.get('exclusiveOwnedShare', 0):.4f}` "
                f"doubleClaimed=`{item.get('doubleClaimedShare', 0):.4f}` "
                f"declaredOverlay=`{item.get('declaredOverlayShare', 0):.4f}` "
                f"undeclaredPartitionOverlap=`{item.get('undeclaredPartitionOverlapShare', 0):.4f}` "
                f"blockedControls=`{item.get('blockedCriticalControlCount', 0)}` "
                f"foreignInterceptedControls=`{item.get('foreignInterceptedCriticalControlCount', 0)}`"
            )
            for overlap in item.get("paintedOwnershipOverlapMatrix", [])[:4]:
                lines.append(
                    "    - "
                    f"`{overlap.get('occludedSlot')}` occluded by "
                    f"`{overlap.get('occludingSlot')}` "
                    f"rootShare=`{overlap.get('shareOfRoot', 0):.4f}` "
                    f"nodeShare=`{overlap.get('shareOfOccludedNode', 0):.4f}`"
                )
            for partition_overlap in item.get("partitionOverlapByUnit", [])[:4]:
                lines.append(
                    "    - Undeclared partition overlap (enforced): "
                    f"`{partition_overlap.get('unitId')}` "
                    f"rootShare=`{partition_overlap.get('shareOfRoot', 0):.4f}`"
                )
            for budget in item.get("overlayBudgetExceeded", [])[:4]:
                lines.append(
                    "    - Overlay budget exceeded (enforced): "
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

    if str(report_detail or REPORT_DETAIL_COMPACT).lower() == REPORT_DETAIL_COMPACT:
        append_compact_measurement_index(lines, report.get("measurements") or [])
        md_path.write_text("\n".join(lines), encoding="utf-8")
        return json_path, md_path

    lines.append("## All trial measurements")
    lines.append("")
    for item in report["measurements"]:
        facts = item.get("geometryFacts", {})
        classification = item.get("classification", {})
        lines.append(f"### `{item['hierarchyId']}` / `{item['viewportProfile']}` / `{item['candidate']}`")
        lines.append("")
        lines.append(f"- Status: `{classification.get('status')}`")
        lines.append(f"- Score: `{classification.get('score')}`")
        lines.append(
            f"- Unrounded selection score: `{float(classification.get('selectionScoreRaw', classification.get('score', 0)) or 0):.4f}`"
        )
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
        lines.append(
            f"- Active-presentation unclaimed ratio: `{facts.get('activePresentationUnclaimedRatio', facts.get('unclaimedAreaRatio', 0)):.4f}`"
        )
        lines.append(
            f"- Root-wide unclaimed ratio (diagnostic): `{facts.get('rootUnclaimedAreaRatio', facts.get('unclaimedAreaRatio', 0)):.4f}`"
        )
        lines.append(
            f"- Intentional inactive root ratio: `{facts.get('intentionalInactiveRootRatio', 0):.4f}`"
        )
        lines.append(
            f"- Accidental unclaimed root ratio: `{facts.get('accidentalUnclaimedRootRatio', facts.get('unclaimedAreaRatio', 0)):.4f}`"
        )
        lines.append(
            f"- Active presentation: mode=`{facts.get('activePresentationMode', 'root-fallback')}` "
            f"unit=`{facts.get('activePresentationUnitId', '')}` "
            f"occupancy=`{facts.get('activePresentationOccupancy', 0):.4f}`"
        )
        lines.append(f"- Node coverage ratio: `{facts.get('nodeCoverageRatio', 0):.4f}`")
        lines.append(f"- Focus share: `{facts.get('focusShare', 0):.4f}`")
        painted = facts.get("paintedOwnership") or facts.get("paintedOwnershipShadow") or {}
        if facts.get("paintedOwnershipEnforced"):
            lines.append("- Painted ownership diagnostics (enforced):")
            lines.append(
                f"  - Raw focus share: `{facts.get('rawFocusShare', painted.get('rawFocusShare', facts.get('focusShare', 0))):.4f}`"
            )
            lines.append(
                f"  - Effective focus share: `{facts.get('effectiveFocusShare', painted.get('effectiveFocusShare', facts.get('focusShare', 0))):.4f}`"
            )
            lines.append(
                f"  - Focus occluded share: `{facts.get('focusOccludedShare', painted.get('focusOccludedShare', 0)):.4f}`"
            )
            lines.append(
                f"  - Exclusive owned share: `{facts.get('exclusiveOwnedShare', painted.get('exclusiveOwnedShare', 0)):.4f}`"
            )
            lines.append(
                f"  - Double-claimed share: `{facts.get('doubleClaimedShare', painted.get('doubleClaimedShare', 0)):.4f}`"
            )
            lines.append(
                f"  - Declared overlay share: `{facts.get('declaredOverlayShare', painted.get('declaredOverlayShare', 0)):.4f}`"
            )
            lines.append(
                f"  - Undeclared partition overlap share: `{facts.get('undeclaredPartitionOverlapShare', painted.get('undeclaredPartitionOverlapShare', 0)):.4f}`"
            )
            lines.append(
                f"  - Partition-overlap cell share: `{facts.get('partitionOverlapCellShare', painted.get('partitionOverlapCellShare', 0)):.4f}`"
            )
            lines.append(
                f"  - Blocked critical controls: `{facts.get('blockedCriticalControlCount', 0)}`"
            )
            lines.append(
                f"  - Fully foreign-intercepted critical controls: `{facts.get('interceptedCriticalControlCount', 0)}`"
            )
            lines.append(
                f"  - Critical controls with any foreign interception: `{facts.get('foreignInterceptedCriticalControlCount', 0)}`"
            )
            outcome_totals = facts.get("controlInterceptionOutcomeTotals") or {}
            lines.append(
                "  - Control hit-test outcomes: "
                f"selfOwned=`{outcome_totals.get('selfOwned', 0)}` "
                f"foreignIntercepted=`{outcome_totals.get('foreignIntercepted', 0)}` "
                f"noPointerTarget=`{outcome_totals.get('noPointerTarget', 0)}` "
                f"pointerEventsNonePassThrough=`{outcome_totals.get('pointerEventsNonePassThrough', 0)}` "
                f"unownedPointerTarget=`{outcome_totals.get('unownedPointerTarget', 0)}`"
            )
            for overlap in painted.get("overlapMatrix", [])[:8]:
                lines.append(
                    "  - Overlap: "
                    f"`{overlap.get('occludedSlot')}` occluded by "
                    f"`{overlap.get('occludingSlot')}` "
                    f"rootShare=`{overlap.get('shareOfRoot', 0):.4f}` "
                    f"nodeShare=`{overlap.get('shareOfOccludedNode', 0):.4f}`"
                )
            for partition_overlap in painted.get(
                "partitionOverlapByUnit", []
            )[:4]:
                lines.append(
                    "  - Undeclared partition overlap: "
                    f"`{partition_overlap.get('unitId')}` "
                    f"rootShare=`{partition_overlap.get('shareOfRoot', 0):.4f}`"
                )
            for budget in painted.get("overlayBudgetExceeded", [])[:4]:
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
        phase_ownership_rows = [
            phase_item
            for phase_item in (item.get("phaseMeasurements") or [])
            if bool((phase_item.get("geometryFacts") or {}).get("paintedOwnershipEnforced"))
        ]
        if phase_ownership_rows:
            lines.append("- Painted ownership by browser phase (enforced):")
            for phase_item in phase_ownership_rows:
                phase_facts = phase_item.get("geometryFacts") or {}
                phase_painted = (
                    phase_facts.get("paintedOwnership")
                    or phase_facts.get("paintedOwnershipShadow")
                    or {}
                )
                lines.append(
                    f"  - `{phase_item.get('phase', 'default')}`: "
                    f"rawFocus=`{phase_facts.get('rawFocusShare', phase_painted.get('rawFocusShare', phase_facts.get('focusShare', 0))):.4f}` "
                    f"effectiveFocus=`{phase_facts.get('effectiveFocusShare', phase_facts.get('focusShare', 0)):.4f}` "
                    f"focusOccluded=`{phase_facts.get('focusOccludedShare', phase_painted.get('focusOccludedShare', 0)):.4f}` "
                    f"exclusiveOwned=`{phase_facts.get('exclusiveOwnedShare', phase_painted.get('exclusiveOwnedShare', 0)):.4f}` "
                    f"doubleClaimed=`{phase_facts.get('doubleClaimedShare', phase_painted.get('doubleClaimedShare', 0)):.4f}` "
                    f"undeclaredPartitionOverlap=`{phase_facts.get('undeclaredPartitionOverlapShare', phase_painted.get('undeclaredPartitionOverlapShare', 0)):.4f}` "
                    f"blockedControls=`{phase_facts.get('blockedCriticalControlCount', 0)}`"
                )
                for overlap in phase_painted.get("overlapMatrix", [])[:4]:
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
            lines.append(
                f"  - Score: `{phase_fit.get('score', 0)}` "
                f"policyRaw=`{float(phase_fit.get('policyScoreRaw', phase_fit.get('score', 0)) or 0):.4f}` "
                f"meanRaw=`{float(phase_fit.get('meanScoreRaw', phase_fit.get('meanScore', 0)) or 0):.4f}` "
                f"worstRaw=`{float(phase_fit.get('worstScoreRaw', phase_fit.get('worstScore', 0)) or 0):.4f}` "
                f"variance=`{float(phase_fit.get('scoreVariance', 0) or 0):.4f}` "
                f"state=`{phase_fit.get('state', 'unknown')}`"
            )
            lines.append(
                f"  - Dominant-share margins: "
                f"worst=`{float(phase_fit.get('worstDominantHeadroom', -1) or 0):+.4f}` "
                f"rawWorst=`{float(phase_fit.get('worstRawDominantHeadroom', phase_fit.get('worstDominantHeadroom', -1)) or 0):+.4f}` "
                f"mean=`{float(phase_fit.get('meanDominantHeadroom', -1) or 0):+.4f}` "
                f"selectedDefault=`{float(phase_fit.get('selectedDefaultHeadroom', -1) or 0):+.4f}` "
                f"floorFailures=`{int(phase_fit.get('phaseFloorFailureCount', 0) or 0)}` "
                f"tolerance=`{float(phase_fit.get('phaseFloorTolerance', PHASE_SHARE_FLOOR_TOLERANCE)):.4f}`"
            )
            for phase_row in phase_fit.get("phases", []):
                lines.append(
                    f"  - Phase `{phase_row.get('phase', 'default')}`: "
                    f"score=`{phase_row.get('score', 0)}` "
                    f"raw=`{float(phase_row.get('rawScore', phase_row.get('score', 0)) or 0):.4f}` "
                    f"dominant=`{float(phase_row.get('dominantShare', 0) or 0):.4f}` "
                    f"floor=`{float(phase_row.get('minDominantShare', 0) or 0):.4f}` "
                    f"floorMet=`{bool(phase_row.get('dominantFloorMet', False))}` "
                    f"rawHeadroom=`{float(phase_row.get('dominantRawHeadroom', phase_row.get('dominantHeadroom', 0)) or 0):+.4f}` "
                    f"headroom=`{float(phase_row.get('dominantHeadroom', 0) or 0):+.4f}`"
                )
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
    parser.add_argument("--viewports", default=DEFAULT_VIEWPORTS, help="Comma-separated base profiles like desktop=1440x900.")
    parser.add_argument(
        "--responsive-mode",
        choices=["off", "recursive", "all"],
        default=DEFAULT_RESPONSIVE_MODE,
        help=(
            "Run capacity-band resize probes for recursive hierarchies by default; "
            "use 'all' for every hierarchy or 'off' for fixed-viewport trials only."
        ),
    )
    parser.add_argument(
        "--responsive-viewports",
        default=DEFAULT_RESPONSIVE_VIEWPORTS,
        help=(
            "Comma-separated resize probes. These are merged with --viewports "
            "for responsive hierarchies."
        ),
    )
    parser.add_argument(
        "--responsive-hysteresis-px",
        type=int,
        default=DEFAULT_RESPONSIVE_HYSTERESIS_PX,
        help="Minimum separation between shrink and grow transition thresholds.",
    )
    parser.add_argument("--chrome", default=DEFAULT_CHROME, help="Chrome/theme label to record as layout input.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output report directory. PNGs are written directly here.")
    parser.add_argument(
        "--screenshot-mode",
        choices=["viewport", "full-page", "both"],
        default="viewport",
        help="Which PNG snapshots to write per trial. Default: viewport.",
    )
    parser.add_argument(
        "--report-detail",
        choices=[REPORT_DETAIL_COMPACT, REPORT_DETAIL_FULL],
        default=REPORT_DETAIL_COMPACT,
        help=(
            "Write a compact trial index by default. Use 'full' only for a "
            "targeted diagnostic run because full browser records can be very large."
        ),
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
    responsive_viewports = (
        parse_viewports(args.responsive_viewports)
        if args.responsive_mode != "off"
        else []
    )

    report = run_synthetic_trials(
        hierarchies=hierarchies,
        candidates=candidates,
        viewports=viewports,
        chrome=args.chrome,
        output_dir=output_dir,
        screenshot_mode=args.screenshot_mode,
        keep_html=args.keep_html,
        responsive_mode=args.responsive_mode,
        responsive_viewports=responsive_viewports,
        responsive_hysteresis_px=args.responsive_hysteresis_px,
    )
    report['rollups'] = generate_rollup_pngs(report, output_dir)
    report['rollupFiles'] = [item['file'] for item in report['rollups']]
    json_path, md_path = write_reports(
        report,
        output_dir,
        report_detail=args.report_detail,
    )
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
