"""Deterministic, experimental information-budget layout for typed tabular values.

No production imports, learned weights, ML calls, repository mutations or UI framework
required. The role/behavior model is an *estimate* of human lookup priorities.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping

ELLIPSIS = "…"
SCHEMA = "mcel.information-budget.experiment.v1"


def _finite_number(value: object, default: float) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError):
        return default
    return result if math.isfinite(result) else default


def text_width(text: str, font_px: float = 14, glyph_widths: Mapping[str, float] | None = None) -> float:
    """Width proxy; browser sends canvas-measured glyph advances for closer estimates."""
    font_px = max(8, min(48, _finite_number(font_px, 14)))
    total = 0.0
    for ch in text:
        if glyph_widths and ch in glyph_widths:
            advance = _finite_number(glyph_widths[ch], 0)
            if 0 < advance < 100:
                total += advance
                continue
        if ch in "ilI1|.,:'`! ":
            ratio = 0.33
        elif ch in "MW@%#":
            ratio = 0.88
        elif ch.isupper():
            ratio = 0.67
        elif ch.isdigit():
            ratio = 0.57
        elif ch in "_-()[]/\\":
            ratio = 0.42
        else:
            ratio = 0.55
        total += font_px * ratio
    return round(total + (2 if text else 0), 3)


def tokenize(value: str) -> list[str]:
    """Split at semantic separators, maintaining the original spelling for return."""
    return [part for part in re.split(r"(?<=[_./\\ -])|(?=[_./\\ -])", value) if part]


def _unit_values(number: float, kind: str) -> list[str]:
    if kind != "size":
        return [str(number)]
    if not math.isfinite(number) or number < 0:
        return ["Unknown"]
    count = int(number)
    if count < 1024:
        return [f"{count:,} B"]
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit_index = min(5, int(math.log(max(count, 1), 1024)))
    scaled = count / (1024 ** unit_index)
    return [f"{scaled:.{precision}f} {units[unit_index]}" for precision in (0, 1, 2, 3)] + [f"{count:,} B"]


def _unit_label(value: object, all_values: list[object], max_width: float, measure: Callable[[str], float]) -> tuple[str, bool]:
    """Compact numeric label, adapt precision to avoid collisions of distinct sizes."""
    n = _finite_number(value, float("nan"))
    if not math.isfinite(n):
        return "Unknown", False
    candidates = _unit_values(n, "size")
    competing = [float(other) for other in all_values if _finite_number(other, float("nan")) != n and math.isfinite(_finite_number(other, float("nan")))]
    for label in candidates:
        if measure(label) > max_width:
            continue
        # Candidate is acceptable only when its own rendering is not shared by distinct values.
        index = candidates.index(label)
        ambiguity = any(_unit_values(other, "size")[min(index, 4)] == label for other in competing)
        if not ambiguity:
            return label, True
    fitting = [label for label in candidates if measure(label) <= max_width]
    return (fitting[-1] if fitting else min(candidates, key=measure)), False


def _token_suffixes(value: str) -> list[str]:
    tokens = tokenize(value)
    return ["".join(tokens[i:]) for i in range(len(tokens)) if tokens[i] not in {"_", "-", " ", "/", "\\", "."}]


def _distinctive_core(name: str, names: list[str]) -> str:
    """The shortest suffix unique among *all* records, not the visible viewport."""
    alternatives = _token_suffixes(name)
    for suffix in sorted(alternatives, key=lambda v: (len(v), v)):
        if all(other == name or not other.endswith(suffix) for other in names):
            return suffix
    return name


def _identifier_candidates(name: str, names: list[str]) -> list[tuple[str, str]]:
    if not name:
        return [("", "empty")]
    core = _distinctive_core(name, names)
    output: list[tuple[str, str]] = [(name, "full")]
    if core != name:
        output.append((ELLIPSIS + core, "unique-suffix"))
    tokens = tokenize(name)
    suffixes = _token_suffixes(name)
    # Candidate widths are not guessed from character counts in the selector.
    for suffix in suffixes:
        if suffix != name:
            output.append((ELLIPSIS + suffix, "suffix"))
    for i in range(1, min(12, len(tokens)) + 1):
        head = "".join(tokens[:i]).rstrip("_- /\\.")
        if not head:
            continue
        for tail in suffixes:
            if len(head) + len(tail) < len(name) and not name.startswith(head + tail):
                output.append((head + ELLIPSIS + tail, "head-tail"))
    # If absolutely necessary, shorten at character boundaries; distinguishability
    # is checked again in the row-set after selecting alternatives.
    for k in range(2, min(len(name), 64)):
        output.append((ELLIPSIS + name[-k:], "character-suffix"))
    dedup: dict[str, str] = {}
    for label, reason in output:
        dedup.setdefault(label, reason)
    return list(dedup.items())


def _label_value(name: str, peers: list[str], px_budget: float, measure: Callable[[str], float], stable_id: str = "") -> dict:
    if measure(name) <= px_budget:
        return {"display": name, "strategy": "full", "uniqueness": "exact", "full": name}
    shortest_unique = _distinctive_core(name, peers)
    candidates = _identifier_candidates(name, peers)
    choices = []
    number_peers = max(1, len(peers))
    for label, strategy in candidates:
        if measure(label) > px_budget:
            continue
        # The equivalence class of a display predicts how many records a person
        # might mistake for this one.  Information gained in bits is the decrease
        # in uncertainty across the *whole stable cohort*: log2(N / matches).
        # Syntax is not meaning: this is a falsifiable approximation of recognition.
        if strategy == "full":
            matches = sum(p == label for p in peers)
        elif ELLIPSIS in label:
            prefix, tail = label.split(ELLIPSIS, 1)
            matches = sum(p.startswith(prefix) and p.endswith(tail) for p in peers)
        else:
            matches = sum(p == label for p in peers)
        matches = max(1, matches)
        distinguishing = matches == 1
        bits = math.log2(number_peers / matches) if number_peers >= matches else 0.0
        cost = (0 if distinguishing else 10_000) - 12 * bits + (0 if strategy == "full" else 3) + (0 if shortest_unique in label else 7)
        # Prefer information retention and stable token-boundary breaks.
        cost += 0 if strategy in ("full", "unique-suffix", "head-tail") else (4 if strategy == "suffix" else 18)
        cost -= 0.015 * measure(label)
        choices.append((cost, label, strategy, distinguishing, bits, matches))
    if choices:
        _, result, strategy, unique, bits, matches = min(choices, key=lambda x: (x[0], x[1]))
        return {"display": result, "strategy": strategy, "uniqueness": "predicted" if unique else "ambiguous",
                "distinguishing_bits_estimate": round(bits, 3), "estimated_cohort_collisions": matches-1, "full": name}
    # A name might have no readable signature at the specified budget.
    fallback = ELLIPSIS
    return {"display": fallback, "strategy": "insufficient-space", "uniqueness": "ambiguous", "full": name}


def infer_lookup(columns: list[dict], behavior: dict) -> list[dict]:
    """Explainable, deterministic proxy for a human's hidden lookup priorities."""
    task = str(behavior.get("task", "browse"))
    sort_by = str(behavior.get("sort_by", ""))
    selected = behavior.get("selected_id") is not None
    actions = [str(a) for a in behavior.get("observed_actions", []) if isinstance(a, str)][:100]
    scores = []
    for col in columns:
        key = str(col.get("key", ""))
        kind = str(col.get("kind", "text"))
        reasons = ["visible data field"]
        score = 1.0
        if kind == "identifier":
            score += 2.7
            reasons.append("identifier used to recognize and select objects")
        if kind == "size":
            score += 1.9
            reasons.append("numeric magnitude supports comparisons")
        if kind in ("date", "timestamp"):
            score += 0.65
            reasons.append("time can distinguish versions")
        if col.get("required"):
            score += 3
            reasons.append("required column is protected")
        if key == sort_by:
            score += 2.2
            reasons.append("current sort depends on this field")
        if any(action == "sort:" + key for action in actions):
            score += 0.9
            reasons.append("observed sort interaction")
        if task == "inspect" and selected:
            score += 0.6
            reasons.append("selected object requires inspection")
        if task == "compare" and kind in ("size", "date", "timestamp", "number"):
            score += 1.3
            reasons.append("comparison task depends on comparable values")
        scores.append({"key": key, "kind": kind, "priority": round(score, 3), "reasons": reasons,
                       "confidence": "heuristic-not-human-validated"})
    return sorted(scores, key=lambda x: (-x["priority"], x["key"]))


def _int_in_range(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def evaluate(payload: dict) -> dict:
    """Side-effect-free machine API. All displayed values map to their originals."""
    if not isinstance(payload, dict):
        raise ValueError("input must be an object")
    rows = payload.get("rows")
    columns = payload.get("columns")
    if not isinstance(rows, list) or not rows or len(rows) > 500 or not isinstance(columns, list) or not (1 <= len(columns) <= 12):
        raise ValueError("requires 1–500 rows and 1–12 columns")
    if any(not isinstance(r, dict) for r in rows) or any(not isinstance(c, dict) for c in columns):
        raise ValueError("rows and columns must be objects")
    if any(len(str(v)) > 4096 for row in rows for v in row.values()):
        raise ValueError("field values must be <=4096 characters")
    keys = [str(c.get("key", "")) for c in columns]
    if not all(keys) or len(set(keys)) != len(keys):
        raise ValueError("column keys must be nonempty and unique")
    ids = [str(r.get("id", i)) for i, r in enumerate(rows)]
    if len(set(ids)) != len(ids):
        raise ValueError("row ids must be unique")
    behavior = payload.get("behavior") or {}
    if not isinstance(behavior, dict):
        raise ValueError("behavior must be an object")
    viewport = _int_in_range(payload.get("viewport_px"), 1100, 260, 4096)
    nav_px = _int_in_range(payload.get("navigation_px"), 192, 0, 500)
    inspector_requested = bool(payload.get("inspector_open", True))
    inspector_mode = str(payload.get("inspector_mode", "open"))
    if inspector_mode not in ("auto", "open", "hidden"):
        raise ValueError("inspector_mode must be auto, open, or hidden")
    inspector_px = _int_in_range(payload.get("inspector_px"), 320, 0, 750)
    font_px = _int_in_range(payload.get("font_px"), 14, 10, 36)
    glyphs = payload.get("glyph_widths") or {}
    if not isinstance(glyphs, dict) or len(glyphs) > 1024:
        raise ValueError("glyph_widths must be a small object")
    glyphs = {str(k): float(v) for k, v in glyphs.items() if len(str(k)) == 1 and 0 < _finite_number(v, -1) < 100}
    measure = lambda s: text_width(str(s), font_px, glyphs)
    lookup = infer_lookup(columns, behavior)
    rank = {item["key"]: item["priority"] for item in lookup}
    by_key = {str(col["key"]): col for col in columns}
    avail = viewport - nav_px - 44
    if avail < 245:
        nav_px = 0
        avail = viewport - 32
    required = [key for key in keys if by_key[key].get("required")]
    mandatory = [key for key in keys if key != next((c for c in keys if by_key[c].get("kind") == "identifier"), None)]
    # Information-preserving representation budgets are set before the longest
    # value is shortened; text-length itself is never allowed to push a column out.
    widths: dict[str, int] = {}
    for key in keys:
        col = by_key[key]
        kind = col.get("kind", "text")
        labels = [str(col.get("label", key))]
        if kind == "size":
            labels += ["999.9 MB", "12,345 B"]
        elif kind in ("timestamp", "date"):
            labels += ["2026-10-25"]
        minimum = _int_in_range(col.get("min_px"), int(max(map(measure, labels)) + 20), 50, 500)
        if kind == "identifier":
            minimum = max(minimum, 142)
        widths[key] = minimum
    gaps = 12 * (len(columns)-1)
    columns_min = sum(widths.values()) + gaps
    inspector_active = inspector_requested and inspector_mode != "hidden"
    notes = []
    if inspector_mode == "auto" and inspector_active and str(behavior.get("task", "browse")) == "browse":
        selected = next((r for i, r in enumerate(rows) if str(r.get("id", i)) == str(behavior.get("selected_id"))), None)
        # An inspector that only repeats displayed fields does not earn a permanent
        # rail when the human is browsing a collection. It remains available
        # manually or when inspection is the active task.
        if selected is None or all(str(k) == "id" or k in keys for k in selected):
            inspector_active = False
            notes.append("Redundant inspector deferred; all selected values remain available on demand")
    if inspector_active and avail - inspector_px < columns_min:
        inspector_active = False
        notes.append("Inspector collapsed to protect required table fields")
    table_space = avail - (inspector_px + 12 if inspector_active else 0)
    layout = "table"
    if table_space < columns_min:
        layout = "stacked"
        notes.append("Compact record presentation: cannot fit all required columns side by side")
        widths = {key: max(120, table_space - 20) for key in keys}
    else:
        remaining = table_space - columns_min
        # Spread excess room based on what we infer the human looks up and the
        # actual payoff in keeping high-information labels readable.
        weighted = [max(0.1, rank[key]) * (3.0 if by_key[key].get("kind") == "identifier" else 0.6) for key in keys]
        denominator = sum(weighted)
        for i, key in enumerate(keys):
            delta = int(remaining * weighted[i]/denominator)
            widths[key] += delta
        widths[keys[0]] += max(0, table_space-gaps-sum(widths.values()))
    names_by_key = {key: [str(r.get(key, "")) for r in rows] for key in keys}
    # Solve the largest text representations first across *all* string/numeric
    # variables. Column widths were already protected, so a long name cannot
    # consume a size column. Representations can then be optimized independently.
    out = [{"id": ids[i], "cells": {}} for i in range(len(rows))]
    work = sorted(((measure(str(row.get(key, ""))), i, key) for i, row in enumerate(rows) for key in keys),
                  key=lambda v: (-v[0], ids[v[1]], v[2]))
    for _, i, key in work:
        row = rows[i]
        kind = by_key[key].get("kind", "text")
        raw = row.get(key, "")
        budget = max(20, widths[key] - 22)
        if kind == "size":
            label, unique = _unit_label(raw, [r.get(key) for r in rows], budget, measure)
            matches = sum(_finite_number(r.get(key), float("nan")) == _finite_number(raw, float("nan")) for r in rows)
            bits = math.log2(max(1,len(rows))/max(1,matches))
            cell = {"display": label, "full": str(raw), "strategy": "human-size-unit",
                    "uniqueness": "predicted" if unique else "ambiguous",
                    "distinguishing_bits_estimate": round(bits, 3)}
        else:
            cell = _label_value(str(raw), names_by_key[key], budget, measure, ids[i])
            if kind not in ("identifier", "text"):
                cell["strategy"] = "exact-or-truncated-typed-value" if cell["display"] != str(raw) else "full"
        cell["rendered_px_estimate"] = round(measure(cell["display"]), 2)
        cell["width_budget_px"] = budget
        out[i]["cells"][key] = cell
    for item in out:
        item["cells"] = {key: item["cells"][key] for key in keys}
    # Compression order is an auditable estimate of the largest presentation
    # savings. It is not a reordering of machine records or source values.
    representation_decisions = []
    compression_trace = []
    for i, record in enumerate(out):
        for key in keys:
            cell = record["cells"][key]
            original = str(rows[i].get(key, ""))
            orig_width = measure(original)
            rendered_width = cell["rendered_px_estimate"]
            if cell["display"] != original:
                decision = {"row_id": record["id"], "column": key,
                            "original_px": round(orig_width, 2),
                            "display_px": round(rendered_width, 2),
                            "saved_px": round(orig_width-rendered_width, 2),
                            "strategy": cell["strategy"],
                            "full_value": original, "display": cell["display"]}
                representation_decisions.append(decision)
                if orig_width - rendered_width > 0.01:
                    compression_trace.append(decision)
    representation_decisions.sort(key=lambda item: (-item["original_px"], item["row_id"], item["column"]))
    compression_trace.sort(key=lambda item: (-item["original_px"], -item["saved_px"], item["row_id"], item["column"]))
    # If two rendered records truly look identical across the whole table,
    # distinguish them using the stable machine ID, not viewport-dependent row order.
    collisions: dict[tuple, list[dict]] = {}
    for r in out:
        signature = tuple(r["cells"][key]["display"] for key in keys)
        collisions.setdefault(signature, []).append(r)
    for same in collisions.values():
        if len(same) < 2:
            continue
        for item in same:
            key = next((c for c in keys if by_key[c].get("kind") == "identifier"), keys[0])
            short_id = hashlib.sha256(item["id"].encode("utf-8")).hexdigest()[:5]
            item["disambiguator"] = "#" + short_id
            item["cells"][key]["uniqueness"] = "requires-disambiguator"
        notes.append("Unresolvable visible labels received stable row-ID disambiguators")
    return {
        "schema": SCHEMA,
        "status": "EXPERIMENTAL_NOT_CERTIFIED",
        "viewport_px": viewport,
        "layout": layout,
        "inspector_visible": inspector_active,
        "table_width_px": table_space,
        "column_widths_px": widths,
        "predicted_human_lookup": lookup,
        "rows": out,
        "notes": sorted(set(notes)),
        "compression_longest_first": compression_trace,
        "representation_decisions": representation_decisions,
        "constraints": {
            "full_values_preserved": True,
            "identifiers_stable_against_scroll": True,
            "size_sort_uses_raw_numeric_value": True,
            "production_integrated": False,
        }
    }
