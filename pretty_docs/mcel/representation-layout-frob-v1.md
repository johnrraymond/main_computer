# MCEL representation-to-layout link, Patch 2

Patch 1 established a semantic-information contract for registered MCEL collection,
collection-row and field elements. Patch 2 consumes *its actual projected text* in
a browser-measured layout allocator. Neither patch alters an installed app or
Website Builder; both currently run in a stand-alone Frob, not production.

## Run one command

From the Main Computer repository root:

```text
python tools/mcel_representation_layout_frob.py
```

On Windows, Playwright can use an installed Edge or Chrome; on other systems it
can use an installed Chromium-family executable. Override browser discovery
with `--chromium-executable PATH`, or install Playwright Chromium with
`python -m playwright install chromium`.

```text
python -m pytest -q tests/test_mcel_representation_layout_contract.py tests/test_mcel_semantic_information_contract.py tests/test_mcel_text_shrinker_adapter.py
python -m webbrowser runtime/reports/mcel-representation-layout/gallery.html
```

Artifacts in `runtime/reports/mcel-representation-layout/`:
`report.json`, before/after HTML/JSON/PNG files, `gallery.html`, and
`representation-layout-review.zip`. `--no-browser` generates inspection files
but marks the result **UNVERIFIED**, never PASS.

## Contract and measurements

The flat file `tools/fixtures/mcel_text_shrinker_widgets.json` remains the sole
source of putative semantic context. Rows, IDs, exact machine values, and action
semantics remain invariant. The layout adapter runs after the existing
`project_collection` projection; the same browser algorithm handles shrinker
on/off. No field is programmatically suppressed by its semantic `required` flag.

At each render or viewport resize, the layout runtime measures *displayed* text
widths using browser font metrics; derives intrinsic widths of registered MCEL
fields; computes available row width; and distributes spare pixels across
columns using declared semantic requirements. It favors protected, identifying
information over redundant prefixes. This is a genuine representation-to-layout
feedback link, not merely fewer characters in an otherwise fixed grid.

On narrow viewports (panel under 500px), the collection stacks fields and wraps
content. At intermediate widths it displays a single-row table, making any
unsatisfied intrinsic width a measurable clipping error. Wider viewports
allocate surplus width by semantic priority. The Frob checks actual DOM element
geometry and visibility in Chromium, not a Python character-count proxy.

## Assertions

| Obligation | Browser measurement |
| --- | --- |
| Space allocation | Required text and field rectangles remain inside the collection; gained column width is recorded |
| Contextual compression | The same row's intrinsic width falls; a *different required field* gains space |
| Identity preservation | Original row/field IDs survive, identifiers stay distinguishable; raw-value search, typed sort and exact inspector still work |
| Responsive layout | Real browser screenshots at 320, 390, 560, 640, 1100px, plus a live 560→390→1100→560 resize sequence |
| Negative control | At 560/640px, with shrinker off, required fields actually clip. With it on, the same fields are fully visible |

This is an adversarial experimental test: the disabled mode is expected to fail
required-field visibility at selected widths, and the overall Frob passes only
when **both** the negative failure and enabled repair occur. The actual lost
fields and column-width gains appear in `report.json`; they must not be
hardcoded as PASS.

A successful run certifies this controlled collection/fixture/browser experiment
only. It does not establish production MCEL rollout, universal viewport
coverage, human comprehension, accessibility equivalence, or a proven
information-theoretic optimum. Those remain separate obligations.
