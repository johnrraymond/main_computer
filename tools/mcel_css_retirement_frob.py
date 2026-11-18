"""Read-only, declaration-by-declaration CSS pixel differential for MCEL legacy styles.

Requires the existing Playwright harness plus optional audit-only Pillow/tinycss2:
    python -m pip install Pillow tinycss2

Run from repository root:
    python tools/mcel_css_retirement_frob.py --output runtime/reports/mcel-css-audit
    python tools/mcel_css_retirement_frob.py --apps calculator,slh --widths 1040 --limit 20

Never edits application CSS. Every probe removes precisely ONE source declaration
from an ephemeral browser stylesheet and compares lossless full-page PNG pixels.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

try:
    import tinycss2
    from PIL import Image, ImageChops
    from playwright.sync_api import sync_playwright
except ImportError as exc:
    raise SystemExit(f"CSS frob requires Playwright, Pillow, and tinycss2: {exc}. "
                     "Install audit-only packages: python -m pip install Pillow tinycss2") from exc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
WORKBENCH = "main_computer/web/applications/styles/mcel-semantic-workbench.css"
APP_STYLES = {"calculator": WORKBENCH, "file-manager": WORKBENCH,
              "task-manager": WORKBENCH, "code-editor": WORKBENCH, "slh": WORKBENCH}
STATES = {"calculator": ("basic", "graphing", "symbolic", "help"),
          "file-manager": ("default",), "task-manager": ("server-processes", "all-processes", "connections", "cpu-gpu"),
          "code-editor": ("default",),
          "slh": ("overview", "worker-bank", "network", "network-worker-bank", "remote-seh")}

THEME = {"color", "background-color", "border-color", "border-top-color", "border-right-color",
         "border-bottom-color", "border-left-color", "outline-color", "text-decoration-color",
         "fill", "stroke", "caret-color", "accent-color"}
CHROME = {"background", "background-image", "box-shadow", "text-shadow", "filter", "backdrop-filter",
          "mask", "mask-image", "border-image", "opacity"}
FORM = {"margin", "padding", "width", "height", "min-width", "min-height", "max-width", "max-height",
        "display", "gap", "row-gap", "column-gap", "border-radius", "border-width", "border-style",
        "grid-area", "grid-row", "grid-column", "grid-template", "grid-template-areas", "grid-template-columns",
        "grid-template-rows", "grid-auto-rows", "grid-auto-columns", "grid-auto-flow", "align-items",
        "align-content", "align-self", "justify-items", "justify-content", "justify-self", "flex", "flex-basis",
        "flex-direction", "flex-wrap", "flex-grow", "flex-shrink", "order", "position", "top", "right", "bottom",
        "left", "inset", "overflow", "overflow-x", "overflow-y", "overscroll-behavior", "box-sizing",
        "white-space", "overflow-wrap", "word-break", "text-align", "vertical-align", "transform"}


def responsibility(name: str) -> str:
    """Triage by property, not proof that a presentation law already replaces it."""
    p = name.lower()
    if p.startswith("--"): return "review:Theme/Form/Chrome token"
    if p in THEME or p.endswith("-color") or p == "color-scheme": return "Theme"
    if p == "background": return "Theme/Chrome"
    if p in {"border", "outline"}: return "Theme/Form/Chrome"
    if p in CHROME or p.startswith(("box-shadow", "text-shadow")): return "Chrome"
    if p in FORM or p.startswith(("grid-", "flex-", "padding-", "margin-", "border-radius")):
        return "Form/Layout"
    if p in {"cursor", "pointer-events", "visibility", "content", "user-select", "touch-action"}:
        return "application/interaction"
    if p.startswith(("font", "text-", "line-height", "letter-spacing")):
        return "Theme/typography review"
    return "manual classification"


def _offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def declarations(css: str, source: str) -> list[dict[str, Any]]:
    """Use parsed CSS token offsets; preserve every other byte of the source.

    Unlike CSSOM.removeProperty(), this can delete one declaration even when
    the same property is declared twice in the same rule.
    """
    starts = _offsets(css)
    def off(token):
        return starts[token.source_line - 1] + token.source_column - 1
    found = []

    def walk(rules, conditions=()):
        for rule in rules:
            if rule.type == "at-rule" and rule.content is not None:
                if rule.lower_at_keyword in {"media", "supports", "container", "layer"}:
                    clause = tinycss2.serialize(rule.prelude).strip()
                    walk(tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True),
                         conditions + ((rule.lower_at_keyword, clause),))
                continue
            if rule.type != "qualified-rule":
                continue
            selector = tinycss2.serialize(rule.prelude).strip()
            tokens = rule.content
            decls = [d for d in tinycss2.parse_declaration_list(tokens, skip_comments=True, skip_whitespace=True)
                     if d.type == "declaration"]
            for d in decls:
                start = off(d)
                semicolon = next((t for t in tokens if t.type == "literal" and t.value == ";"
                                  and off(t) >= start), None)
                if semicolon is not None:
                    end = off(semicolon) + 1
                else:
                    # Last declaration is allowed to omit its terminating semicolon.
                    subsequent = [off(t) for t in tokens if off(t) > start]
                    end = (max(subsequent) + len(max((t for t in tokens if off(t) > start),
                                                  key=off).serialize())) if subsequent else None
                if end is None or end <= start or end > len(css):
                    raise ValueError(f"Could not locate {source}:{d.source_line}:{d.name}")
                record = {"file": source, "line": d.source_line, "column": d.source_column,
                          "selector": selector, "conditions": list(conditions),
                          "property": d.lower_name, "value": tinycss2.serialize(d.value).strip(),
                          "important": bool(d.important), "start": start, "end": end,
                          "responsibility": responsibility(d.lower_name)}
                found.append(record)
    walk(tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True))
    for index, item in enumerate(found, 1):
        item["id"] = f"{Path(source).name}:{item['line']}:{item['column']}:{index}"
    return found


def _app_html(root: Path, app: str, state: str) -> str:
    if app == "calculator":
        from main_computer.mcel_calculator_expanded_host import render_expanded_page
        return render_expanded_page(root, promoted=True)
    if app == "slh":
        from main_computer.mcel_slh_application import render_slh_page
        return render_slh_page(state, root)
    from main_computer.mcel_semantic_other_apps_host import render_semantic_page
    view = state if app == "task-manager" else None
    return render_semantic_page(app, root, promoted=True, initial_view=view)


def _instrument(html: str, css: str) -> str:
    if html.count(css) != 1:
        raise ValueError("Source stylesheet not embedded exactly once; refusing an ambiguous CSS probe")
    # Isolate just the real stylesheet in a separately identifiable sheet, in
    # precisely the same cascade position as before (no extra override CSS).
    return html.replace(css, '</style><style id="mcel-css-frob-source">' + css + '</style><style>', 1)


def _pixel_diff(original: bytes, changed: bytes) -> tuple[int, list[int] | None]:
    with Image.open(io.BytesIO(original)) as img:
        a = img.convert("RGB")
    with Image.open(io.BytesIO(changed)) as img:
        b = img.convert("RGB")
    if a.size != b.size:
        return -1, [0, 0, max(a.width, b.width), max(a.height, b.height)]
    if original == changed:
        return 0, None
    diff = ImageChops.difference(a, b)
    bounds = diff.getbbox()
    if not bounds: return 0, None
    channels = diff.split()
    nonzero = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    hist = nonzero.histogram()
    return sum(hist[1:]), list(bounds)


def _eligible(page, item: dict) -> bool | None:
    """False is unexercised, not a safe-to-delete verdict."""
    if any(kind == "container" for kind, _ in item["conditions"]):
        return None  # no reliable container query evaluation from selector alone
    return page.evaluate("""item => {
      for (const [kind, condition] of item.conditions) {
        if(kind === 'media' && !matchMedia(condition).matches) return false;
        if(kind === 'supports' && !CSS.supports(condition)) return false;
      }
      try { return !!document.querySelector(item.selector); }
      catch (_) { return null; }  // e.g. ::before: screenshot instead
    }""", {"selector": item["selector"], "conditions": item["conditions"]})


def _capture(page):
    return page.screenshot(full_page=True, animations="disabled", caret="hide", timeout=30000)


def _stabilize(page):
    page.evaluate("""async () => {
      if(document.fonts && document.fonts.ready) await document.fonts.ready;
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    }""")


def _state(page, app: str, state: str):
    if app == "calculator" and state != "basic":
        page.locator(f'[data-mcel-mode-tabs] button[data-value="{state}"]').first.click(timeout=5000)
    _stabilize(page)
    root_selector = "#mcel-expanded-root" if app == "calculator" else "#mcel-workbench"
    count = page.locator(root_selector + " [data-mcel-region]").count()
    if count == 0:
        raise RuntimeError(f"Application did not mount semantic regions: {app}:{state}")
    if app == "calculator" and "refused" in page.locator("#mcel-expanded-diagnostic").inner_text().lower():
        raise RuntimeError("Calculator mount refused")


def _save(out: Path, records: list[dict], context: dict):
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"schema": "mcel.css.declaration-frob.v1",
        "context": context, "results": records}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = ("file", "line", "column", "selector", "conditions", "property", "value",
              "responsibility", "decision", "matched_states", "tested_states", "changed_states",
              "max_changed_pixels", "first_changed_bbox", "errors")
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow({field: json.dumps(r[field], ensure_ascii=False) if isinstance(r.get(field), (list, dict))
                             else r.get(field, "") for field in fields})


def audit(root: Path, out: Path, *, apps: list[str], widths: list[int],
          limit: int = 0, all_views: bool = False, stability_every: int = 1) -> list[dict]:
    from tools.mcel_phase2b_browser import launch_semantic_browser
    source_text = {name: (root / name).read_text(encoding="utf-8")
                   for name in {APP_STYLES[app] for app in apps}}
    records = [item for name, css in source_text.items() for item in declarations(css, name)]
    records.sort(key=lambda x: (x["file"], x["start"]))
    if limit: records = records[:limit]  # for a quick, honest smoke run
    for item in records:
        item.update(matched_states=0, tested_states=0, changed_states=[], max_changed_pixels=0,
                    first_changed_bbox=None, errors=[], decision="not-tested")
    context = {"apps": apps, "widths": widths, "all_views": all_views, "limit": limit,
               "source_sha256": {name: hashlib.sha256(css.encode("utf-8")).hexdigest()
                                 for name, css in source_text.items()},
               "scope": "full-page screenshot pixels, default scroll position; explicit rendered states only",
               "guarantee": "Zero pixel difference is NOT proof that an untested state can safely lose a declaration.",
               "source_files_unchanged": True}
    count_scenarios = 0
    with sync_playwright() as p:
        browser, browser_source = launch_semantic_browser(p)
        context["browser"] = browser_source
        try:
            for app in apps:
                states = STATES[app] if all_views else STATES[app][:1]
                source = APP_STYLES[app]
                items = [r for r in records if r["file"] == source]
                if not items: continue
                for state in states:
                    html = _instrument(_app_html(root, app, state), source_text[source])
                    for width in widths:
                        page = browser.new_page(viewport={"width": width, "height": 850},
                                                reduced_motion="reduce", device_scale_factor=1)
                        label = f"{app}:{state}:{width}px"
                        try:
                            page.goto("about:blank")
                            page.evaluate("""() => {
                              const map=new Map(); Object.defineProperty(window, 'localStorage',
                                {configurable:true,value:{getItem:k=>map.get(String(k))??null,
                                 setItem:(k,v)=>map.set(String(k),String(v)),
                                 removeItem:k=>map.delete(String(k)), clear:()=>map.clear(),
                                 key:i=>[...map.keys()][i]??null,get length(){return map.size;}}});
                            }""")
                            page.set_content(html, wait_until="domcontentloaded", timeout=30000)
                            _state(page, app, state)
                            # Halt interval/clock-driven UI updates during exact pixel comparisons.
                            # The live application is not modified: this is a disposable page.
                            page.clock.pause_at("2026-11-15T12:00:00")
                            style = page.locator("#mcel-css-frob-source")
                            if style.count() != 1: raise RuntimeError("Missing isolated source stylesheet")
                            initial = _capture(page)
                            print(f"[AUDIT] {label}: {len(items)} declarations", flush=True)
                            unchecked = []
                            for i, item in enumerate(items):
                                is_eligible = _eligible(page, item)
                                if is_eligible is False: continue
                                if is_eligible is None and any(kind == "container" for kind, _ in item["conditions"]):
                                    item["errors"].append(f"{label}: container condition not evaluated")
                                    continue
                                item["matched_states"] += 1
                                prior_error_count = len(item["errors"])
                                css = source_text[source]
                                mutated = css[:item["start"]] + css[item["end"]:]
                                pixels, bounds = 0, None
                                try:
                                    style.evaluate("(el, text) => {el.textContent=text;}", mutated)
                                    pixels, bounds = _pixel_diff(initial, _capture(page))
                                except Exception as exc:
                                    item["errors"].append(f"{label}: {type(exc).__name__}: {exc}")
                                finally:
                                    style.evaluate("(el, text) => {el.textContent=text;}", css)
                                unchecked.append(item)
                                if stability_every and len(unchecked) >= stability_every:
                                    restored = _capture(page)
                                    drift, _ = _pixel_diff(initial, restored)
                                    if drift:
                                        # A transient/reversible screenshot is not enough; do
                                        # not certify ANY declaration since the last stable base.
                                        for candidate in unchecked:
                                            candidate["errors"].append(
                                                f"{label}: post-restore baseline drift ({drift} pixels)")
                                        # Continue testing later declarations against the newly
                                        # settled reference rather than throwing out the whole app.
                                        initial = restored
                                    unchecked.clear()
                                if (i+1) % 50 == 0:
                                    print(f"  {label}: {i+1}/{len(items)}", flush=True)
                                    _save(out, records, context)
                                if len(item["errors"]) == prior_error_count:
                                    item["tested_states"] += 1
                                    if pixels:
                                        item["changed_states"].append({"state": label, "changed_pixels": pixels,
                                                                       "bbox": bounds})
                                        item["first_changed_bbox"] = item["first_changed_bbox"] or bounds
                                        item["max_changed_pixels"] = max(item["max_changed_pixels"], pixels)
                            if unchecked:
                                drift, _ = _pixel_diff(initial, _capture(page))
                                if drift:
                                    for candidate in unchecked:
                                        candidate["errors"].append(
                                            f"{label}: post-restore baseline drift ({drift} pixels)")
                            count_scenarios += 1
                            print(f"[PASS] {label}: {len(items)} declarations considered", flush=True)
                        except Exception as exc:
                            for item in items: item["errors"].append(f"SCENARIO INVALID: {label}: {exc}")
                            print(f"[INVALID] {label}: {exc}", flush=True)
                        finally:
                            page.close()
                        _save(out, records, context)
        finally:
            browser.close()
    context["completed_scenarios"] = count_scenarios
    for item in records:
        if item["errors"]:
            item["decision"] = "UNVERIFIED (test error/drift)"
        elif item["changed_states"]:
            item["decision"] = f"REVIEW for {item['responsibility']} (visible effect)"
        elif item["tested_states"]:
            item["decision"] = "PIXEL-INERT candidate (tested states only)"
        else:
            item["decision"] = "UNEXERCISED (not evidence of redundancy)"
    _save(out, records, context)
    summary = {"total": len(records), "scenarios": count_scenarios,
               "pixel_inert": sum(r["decision"].startswith("PIXEL-INERT") for r in records),
               "visible": sum(r["decision"].startswith("REVIEW") for r in records),
               "unexercised": sum(r["decision"].startswith("UNEXERCISED") for r in records),
               "unverified": sum(r["decision"].startswith("UNVERIFIED") for r in records)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("SUMMARY", json.dumps(summary), "REPORT", str(out), flush=True)
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="Main Computer repository root")
    parser.add_argument("--output", type=Path, default=Path("runtime/reports/mcel-css-retirement"))
    parser.add_argument("--apps", default=",".join(APP_STYLES), help="Comma-separated MCEL application keys")
    parser.add_argument("--widths", default="390,1040,1440", help="Comma-separated CSS pixel widths")
    parser.add_argument("--all-views", action="store_true", help="Include Calculator modes, Task Manager tabs, SLH views")
    parser.add_argument("--limit", type=int, default=0, help="First N declarations only (smoke test)")
    parser.add_argument("--stability-every", type=int, default=1, help="Baseline recheck every N declarations (1 is strictest)")
    args = parser.parse_args(argv)
    apps = [x.strip() for x in args.apps.split(",")]
    invalid = set(apps) - APP_STYLES.keys()
    if invalid: parser.error("Unknown app(s): " + ",".join(sorted(invalid)))
    widths = [int(x) for x in args.widths.split(",")]
    if any(w < 250 for w in widths) or args.limit < 0 or args.stability_every < 0:
        parser.error("Invalid viewport, limit, or stability interval")
    root = args.root.resolve()
    out = args.output if args.output.is_absolute() else root / args.output
    audit(root, out, apps=apps, widths=widths, limit=args.limit,
          all_views=args.all_views, stability_every=args.stability_every)


if __name__ == "__main__":
    main()
