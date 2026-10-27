"""Experimental MCEL semantic text-shrinker adapter, backed by ONE flat JSON file.

No database, background observation, training or production registration. This
is deliberately a call-site adapter around mcel_information_budget.evaluate.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

try:
    from tools.mcel_information_budget import evaluate, text_width, _unit_label
except ModuleNotFoundError:  # python tools/mcel_text_shrinker_adapter.py
    from mcel_information_budget import evaluate, text_width, _unit_label

SCHEMA = "mcel.text-shrinker-adapter.v1"
SEPARATORS = "_-./\\ "


def load_flat_file(path: str | Path) -> dict[str, Any]:
    """Read putative reference values and demo widget semantics without mutation."""
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != "mcel.text-shrinker-fixture.v1":
        raise ValueError("Expected mcel.text-shrinker-fixture.v1 JSON object")
    if not isinstance(doc.get("value_sets"), dict) or not isinstance(doc.get("widgets"), dict):
        raise ValueError("Flat file must have value_sets and widgets objects")
    for key, values in doc["value_sets"].items():
        if not isinstance(key, str) or not isinstance(values, list) or not 2 <= len(values) <= 500:
            raise ValueError(f"Invalid value set: {key!r}")
        if any(not isinstance(item, (str, int, float)) or len(str(item)) > 4096 for item in values):
            raise ValueError(f"Invalid putative values for {key!r}")
    return doc


def _common_affixes(values: list[str]) -> tuple[str, str]:
    """Infer only literal shared affixes; avoid semantically guessing single tokens.

    A prefix/suffix is removable only if EVERY putative value shares it.
    Break on syntax separators; require a remaining distinguishing core per item.
    This is intentionally conservative and explainable.
    """
    if len(set(values)) < 2:
        return "", ""
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
    # Only whole segments are removable; don't chop inside zero_line etc.
    while prefix and prefix[-1] not in SEPARATORS:
        prefix = prefix[:-1]
    stripped = [v[len(prefix):] for v in values]
    suffix = stripped[0]
    for value in stripped[1:]:
        while suffix and not value.endswith(suffix):
            suffix = suffix[1:]
    while suffix and suffix[0] not in SEPARATORS:
        suffix = suffix[1:]
    if suffix and any(len(v) <= len(suffix) + 2 for v in stripped):
        suffix = ""
    if prefix and any(len(v) <= len(prefix) + len(suffix) + 2 for v in values):
        prefix = ""
    return prefix, suffix


def _apply_affixes(value: str, prefix: str, suffix: str) -> str:
    if not prefix and not suffix:
        return value
    if prefix and not value.startswith(prefix):
        return value
    if suffix and not value.endswith(suffix):
        return value
    cropped = value[len(prefix):len(value) - len(suffix) if suffix else len(value)]
    return cropped.strip(SEPARATORS) or value


def adapt_widget(widget: dict, value_sets: dict, *, viewport_px: int = 1100,
                 font_px: int = 14) -> dict:
    """Apply per-field semantic opt-ins to a widget's actual rows.

    Input widget uses columns=[{key, label, kind, required, presentation:{text_shrinker:{enabled,
    values_ref, mode}}}], rows=[{id, ...}] and optional behavior. Output is a
    presentation projection only. Raw rows never change and are returned by ID.
    """
    if not isinstance(widget, dict):
        raise ValueError("widget must be an object")
    columns, source_rows = widget.get("columns"), widget.get("rows")
    if not isinstance(columns, list) or not isinstance(source_rows, list):
        raise ValueError("widget must define columns and rows")
    if not source_rows or len(source_rows) > 500 or len(columns) < 1 or len(columns) > 12:
        raise ValueError("widget requires 1..500 rows and 1..12 columns")
    if any(not isinstance(r, dict) for r in source_rows) or any(not isinstance(c, dict) for c in columns):
        raise ValueError("rows and columns must contain objects")
    if len({str(r.get("id", i)) for i, r in enumerate(source_rows)}) != len(source_rows):
        raise ValueError("row IDs must be unique")
    processed = [dict(row) for row in source_rows]
    core_columns = []
    configurations = {}
    evidence = {}
    for col in columns:
        key = col.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Every column needs a key")
        if key in configurations:
            raise ValueError("Column keys must be unique")
        setting = col.get("presentation", {}).get("text_shrinker", {})
        if not isinstance(setting, dict):
            raise ValueError(f"text_shrinker must be an object for {key}")
        active = setting.get("enabled") is True
        mode = str(setting.get("mode", "contextual_identifier")) if active else "off"
        if active and mode not in ("contextual_identifier", "adaptive_size"):
            raise ValueError(f"Unsupported shrinker mode {mode!r} for {key}")
        configs = {"enabled": active, "mode": mode, "values_ref": setting.get("values_ref")}
        configurations[key] = configs
        base = {k: v for k, v in col.items() if k != "presentation"}
        if not active:
            # Core must never turn an opted-out numeric into human units.
            base["kind"] = "text"
        elif mode == "adaptive_size":
            base["kind"] = "size"
        else:
            base["kind"] = "identifier"
        core_columns.append(base)
        if not active:
            evidence[key] = {"status": "OPTED_OUT"}
            continue
        ref = setting.get("values_ref")
        if not isinstance(ref, str) or ref not in value_sets:
            raise ValueError(f"Enabled field {key!r} requires valid values_ref")
        references = value_sets[ref]
        if not isinstance(references, list) or len(references) < 2:
            raise ValueError(f"{ref} needs at least two putative values")
        if mode == "contextual_identifier":
            prefix, suffix = _common_affixes([str(x) for x in references])
            transformed_references = [_apply_affixes(str(x), prefix, suffix) for x in references]
            # If two different reference identities become indistinguishable, fail
            # closed rather than creating an apparently confident abbreviation.
            if len(set(transformed_references)) != len(set(str(x) for x in references)):
                raise ValueError(f"Putative context {ref} loses identifier distinctions")
            for row in processed:
                row[key] = _apply_affixes(str(row.get(key, "")), prefix, suffix)
            evidence[key] = {"status": "ENABLED", "mode": mode, "values_ref": ref,
                             "shared_prefix": prefix, "shared_suffix": suffix,
                             "reference_count": len(references)}
        else:
            evidence[key] = {"status": "ENABLED", "mode": mode, "values_ref": ref,
                             "reference_count": len(references)}
    behavior = widget.get("behavior", {"task": "browse"})
    options = {"columns": core_columns, "rows": processed, "behavior": behavior,
               "viewport_px": viewport_px, "font_px": font_px,
               "inspector_mode": "auto", "inspector_open": False,
               "navigation_px": 0}
    evaluation = evaluate(options)
    # Importantly, a caller does not consent to formatting merely by declaring
    # a column. Fields not opted in are passed through without modification.
    result_rows = []
    display_collisions = []
    for i, (raw, projected) in enumerate(zip(source_rows, evaluation["rows"])):
        cells = {}
        for col in columns:
            key = col["key"]
            raw_value = raw.get(key, "")
            cell = dict(projected["cells"][key])
            cell["raw"] = raw_value
            cell["full"] = str(raw_value)
            cell["enabled"] = configurations[key]["enabled"]
            if not configurations[key]["enabled"]:
                cell["display"] = str(raw_value)
                cell["strategy"] = "passthrough"
                cell["uniqueness"] = "not-evaluated"
                cell["rendered_px_estimate"] = text_width(cell["display"], font_px)
                cell["over_budget"] = cell["rendered_px_estimate"] > cell["width_budget_px"]
            else:
                cell["contextual_value"] = str(processed[i].get(key, ""))
                if configurations[key]["mode"] == "adaptive_size":
                    # Putative numerical references can affect how much precision
                    # a size needs, even when those peers are not current rows.
                    ref = configurations[key]["values_ref"]
                    peers = list(value_sets[ref]) + [r.get(key) for r in source_rows]
                    budget = cell["width_budget_px"]
                    label, unique = _unit_label(raw_value, peers, budget,
                        lambda text: text_width(text, font_px))
                    cell["display"] = label
                    cell["uniqueness"] = "predicted" if unique else "ambiguous"
                    cell["rendered_px_estimate"] = text_width(label, font_px)
                    cell["format_references"] = len(value_sets[ref])
            cells[key] = cell
        result_rows.append({"id": str(raw.get("id", i)), "cells": cells})
    # Display collisions are a soft finding, not a claim of unique human recognition.
    keys = [c["key"] for c in columns]
    signatures: dict[tuple, list[str]] = {}
    for row in result_rows:
        signatures.setdefault(tuple(row["cells"][k]["display"] for k in keys), []).append(row["id"])
    for ids in signatures.values():
        if len(ids) > 1:
            display_collisions.append(ids)
    return {"schema": SCHEMA, "status": "EXPERIMENTAL", "layout": evaluation["layout"],
            "column_widths_px": evaluation["column_widths_px"], "columns": columns,
            "rows": result_rows, "inference": evidence, "collisions": display_collisions,
            "source_rows_unmodified": True, "production_integrated": False,
            "notes": evaluation["notes"]}


def render_html(result: dict, widget_name: str) -> str:
    """Human preview of the machine projection; never mutates or truncates raw data."""
    cols = result["columns"]
    widths = result["column_widths_px"]
    style = """<style>*,*::before,*::after{box-sizing:border-box}html,body{max-width:100%}details{min-width:0;overflow-wrap:anywhere}body{font:15px system-ui,sans-serif;max-width:1150px;margin:30px auto;padding:0 20px;color:#172231}h1{font-size:22px}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{padding:9px;text-align:left;border-bottom:1px solid #d4dde6;overflow-wrap:anywhere}th{background:#edf2f6}.details{font-size:12px;color:#64748b}tr:nth-child(even){background:#f8fafc}summary{cursor:pointer}.tablewrap{overflow:auto}.record{border:1px solid #d4dde6;border-radius:10px;padding:12px;margin:12px 0}.field{display:grid;grid-template-columns:70px minmax(0,1fr);gap:6px;padding:5px 0}.label{color:#64748b;font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"""
    output = ["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>", style,
              "<title>MCEL text-shrinker experiment</title>",
              f"<h1>MCEL text shrinker: {html.escape(widget_name)}</h1><p>Experimental output. Expand a cell to inspect its exact original value.</p>",
              f"<p>Layout: {html.escape(result['layout'])}; widths: {html.escape(str(widths))}</p>"]
    if result["layout"] == "stacked":
        for row in result["rows"]:
            output.append("<section class='record'>")
            for col in cols:
                cell = row["cells"][col["key"]]
                display = html.escape(str(cell["display"]))
                full = html.escape(str(cell["full"]))
                label = html.escape(str(col.get("label", col["key"])))
                output.append(f"<div class='field'><span class='label'>{label}</span><details><summary title='{full}'>{display}</summary><div class='details'>{full}</div></details></div>")
            output.append("</section>")
    else:
        total_width = sum(widths.get(str(col["key"]), 1) for col in cols)
        output.append("<div class='tablewrap'><table><colgroup>")
        for col in cols:
            pct = 100 * widths.get(str(col["key"]), 1) / max(1, total_width)
            output.append(f"<col style='width:{pct:.2f}%'>")
        output.append("</colgroup><thead><tr>")
        for col in cols:
            output.append(f"<th>{html.escape(str(col.get('label', col['key'])))}</th>")
        output.append("</tr></thead><tbody>")
        for row in result["rows"]:
            output.append("<tr>")
            for col in cols:
                cell = row["cells"][col["key"]]
                display = html.escape(str(cell["display"]))
                full = html.escape(str(cell["full"]))
                strategy = html.escape(str(cell["strategy"]))
                output.append(f"<td><details><summary title='{full}'>{display}</summary><div class='details'>{full}<p>{strategy}</p></div></details></td>")
            output.append("</tr>")
        output.append("</tbody></table></div>")
    output.append("<h2>Inferred flat-file context</h2><pre>")
    output.append(html.escape(json.dumps(result["inference"], indent=2, ensure_ascii=False)))
    output.append("</pre></html>")
    return "".join(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimental MCEL semantic text shrinker from one flat JSON file")
    parser.add_argument("--fixture", default=str(Path(__file__).with_name("fixtures") / "mcel_text_shrinker_widgets.json"))
    parser.add_argument("--widget", default="downloads")
    parser.add_argument("--viewport", type=int, default=1100)
    parser.add_argument("--output", default="runtime/reports/mcel-text-shrinker")
    args = parser.parse_args()
    fixture = load_flat_file(args.fixture)
    if args.widget not in fixture["widgets"]:
        parser.error(f"Unknown widget {args.widget!r}; choose from {', '.join(fixture['widgets'])}")
    result = adapt_widget(fixture["widgets"][args.widget], fixture["value_sets"], viewport_px=args.viewport)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "preview.html").write_text(render_html(result, args.widget), encoding="utf-8")
    print(f"MCEL_TEXT_SHRINKER_EXPERIMENT layout={result['layout']} widget={args.widget} viewport={args.viewport}")
    for row in result["rows"]:
        print(row["id"], " | ".join(f"{col['key']}={row['cells'][col['key']]['display']}" for col in result["columns"]))
    print("report:", out / "result.json")
    print("preview:", out / "preview.html")


if __name__ == "__main__":
    main()
