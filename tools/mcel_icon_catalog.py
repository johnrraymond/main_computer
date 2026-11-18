"""Generate an offline, interactive catalog of the canonical MCEL Icon library.

Example: python -m tools.mcel_icon_catalog --output runtime/reports/mcel-icon-catalog.html
No server, external CSS, icon font, or existing application migration required.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path

from main_computer.mcel_icon import REGISTRY_PATH, get_icon, icon_ids, load_registry, render_icon

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "main_computer/web/applications/scripts/mcel-icon.js"
CSS = ROOT / "main_computer/web/applications/styles/mcel-icon.css"
DEFAULT_REPORT = ROOT / "runtime/reports/mcel-icon-catalog.html"


def make_catalog() -> str:
    """Self-contained HTML; the browser adapter consumes the canonical JSON."""
    registry = load_registry()
    groups = Counter(icon["family"] for icon in registry["icons"])
    cards = []
    for icon_id in icon_ids():
        icon = get_icon(icon_id)
        cards.append(
            f'<article class="icon-card" data-icon-id="{icon_id}" data-icon-family="{icon["family"]}">'
            f'<div class="preview mcel-icon-slot">{render_icon(icon_id, size=36)}</div>'
            f'<div class="label">{escape(icon["label"])}</div>'
            f'<code>{escape(icon_id)}</code></article>'
        )
    # This is static code/JSON shipped with the repository, not user markup.
    js = JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    if "</script" in js.lower() or "</style" in css.lower():
        raise ValueError("MCEL Icon catalog asset contains an unsafe inline terminator")
    serialized = json.dumps(registry, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    family_options = "".join(
        f'<option value="{escape(family)}">{escape(family.title())} ({count})</option>'
        for family, count in groups.items()
    )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCEL Icon · Canonical Catalog</title>
<style>{css}
:root {{ color-scheme: dark; font-family: Arial, Helvetica, sans-serif; background: #0d131c; color: #e8edf4; }}
* {{box-sizing:border-box}}
body {{max-width:1370px; margin:0 auto; padding:28px 24px 64px}}
h1 {{font-size:clamp(24px,3vw,36px);margin:0 0 7px}}
.subtitle {{color:#aebed1;line-height:1.5;margin:0 0 25px;max-width:850px}}
.controls {{display:flex;flex-wrap:wrap;align-items:end;gap:14px;margin-bottom:18px}}
.controls label {{display:flex;flex-direction:column;gap:7px;color:#bac8d8;font-size:13px}}
.controls input,.controls select {{font:inherit;color:#e8edf4;background:#1b2634;border:1px solid #40526a;border-radius:7px;padding:9px 11px}}
.controls input[type=search] {{min-width:min(320px,75vw)}}
.controls input[type=range] {{padding:9px 0;width:130px}}
#count {{color:#afc2d8;font-size:14px;padding-bottom:10px}}
.catalog {{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:12px}}
.icon-card {{min-width:0;border:1px solid #34465b;border-radius:11px;padding:17px 11px 15px;background:#151f2c;text-align:center}}
.icon-card[hidden] {{display:none}}
.preview {{height:74px;margin-bottom:10px;color:#8ad7ff}}
.preview .mcel-icon-svg {{max-width:100%;max-height:100%}}
.icon-card .label {{font-size:14px;line-height:1.45;font-weight:600;min-height:39px}}
.icon-card code {{font:11px/1.5 Consolas,monospace;color:#b3c4d7;overflow-wrap:anywhere;display:block;margin-top:5px}}
.icon-card:focus-within {{outline:2px solid #8ad7ff;outline-offset:2px}}
.footer {{font-size:13px;margin-top:20px;color:#aebed1}}
</style></head>
<body><h1>MCEL Icon</h1>
<p class="subtitle">Canonical semantic drawings for applications, frobers, operations, resources, status, navigation, and interface controls. Icon identity remains independent of pressed state, command execution, and success or failure.</p>
<div class="controls"><label>Find icon<input id="search" type="search" placeholder="Search name or identity" autocomplete="off"></label>
<label>Family<select id="family"><option value="">All families</option>{family_options}</select></label>
<label>Preview size <output id="size-value" for="size">36 px</output><input id="size" type="range" min="16" max="64" value="36" step="4"></label>
<span id="count" role="status" aria-live="polite">{len(cards)} icons</span></div>
<main class="catalog" id="catalog">{''.join(cards)}</main>
<p class="footer">Source: <code>main_computer/web/applications/icons/mcel-icons.v1.json</code> · Schema: <code>{registry['schema']}</code>. This catalog is standalone and does not modify the desktop.</p>
<script id="mcel-icon-registry" type="application/json">{serialized}</script>
<script>{js}</script>
<script>
(() => {{
  "use strict";
  const icons = window.MCELIcon.fromDocument();
  const search = document.getElementById("search");
  const family = document.getElementById("family");
  const size = document.getElementById("size");
  const cards = [...document.querySelectorAll(".icon-card")];
  const count = document.getElementById("count");
  function update() {{
    const query = search.value.trim().toLowerCase();
    const selected = family.value;
    const iconSize = Number(size.value);
    document.getElementById("size-value").textContent = iconSize + " px";
    let visible = 0;
    for (const card of cards) {{
      const info = icons.get(card.dataset.iconId);
      const match = (!selected || info.family === selected) &&
        (!query || (info.id + " " + info.label).toLowerCase().includes(query));
      card.hidden = !match;
      if (match) {{
        visible++;
        const slot = card.querySelector(".preview");
        // Identity and geometry remain fixed; only rendering size changes.
        if (slot.firstElementChild?.getAttribute("width") !== String(iconSize))
          icons.mount(slot, info.id, {{size: iconSize}});
      }}
    }}
    count.textContent = visible + " of " + cards.length + " icons";
  }}
  search.addEventListener("input", update);
  family.addEventListener("change", update);
  size.addEventListener("input", update);
  update();
  document.body.dataset.mcelIconCatalogReady = "true";
}})();
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT,
                        help="Standalone HTML catalog output")
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(make_catalog(), encoding="utf-8")
    counts = dict(Counter(icon["family"] for icon in load_registry()["icons"]))
    print(f"MCEL_ICON_CATALOG PASS icons={len(icon_ids())} families={counts} output={output}")


if __name__ == "__main__":
    main()
