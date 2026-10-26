# MCEL Semantic Generation — executable first slice

This patch adds a working **read-only fixture application generated from author-owned semantics and progressively stronger layout hints**. It does not install or replace the real Task Manager and does not authorize process termination, server mutations, wallet operations, or AI provider calls.

## Run

From the repository root, using the normal virtual environment (Python, PyYAML, Node, Playwright/Chromium):

```text
python tools/mcel_semantic_generate.py --mode verify
python tools/mcel_layout_frob.py --mode semantic
python -m unittest tests.test_mcel_semantic_generation -q
```

The generated files are placed under `runtime/reports/mcel-semantic-generation/` for the first command or `runtime/reports/mcel-layout-frob/semantic-generation/` for the Frob command. Open the generated `preview.html` locally in a browser, or inspect screenshots (`preview-320.png` etc.). The report documents the exact design hash, component roster, candidate rankings, hint decisions, DSL fingerprint and independent Chromium interaction results.

## Authoring

Add hints in `mcel_designs/task-manager/design.yaml`:

```yaml
layout_hints:
  - id: attention-preference
    target: priority-area
    property: position
    value: upper-left
    strength: prefer
    when: desktop
  - id: attention-before-work
    target: priority-area
    property: reading_order
    value: {before: workspaces}
    strength: require
    when: all
```

Supported properties in this first slice: `position: upper-left`, `reading_order: {before: region-id}`, `prominence: high`, and `available_space: maximize`. Supported strengths: `suggest`, `prefer`, `constrain`, `require`. Supported scopes: `all`, `mobile`, `tablet`, `desktop`. Unsupported fields and unknown semantic IDs **refuse**, and unsatisfiable hard hints refuse. The planner records every applicable hint's satisfaction/relaxation. Changing the strength from `suggest` to `require` changes the selected candidate in the regression tests.

There is no app-specific layout CSS: the generated shell is selected from reusable arrangements and mounted by the existing `McelSemanticLayoutInference` canonical layout evaluator. Presentation tokens control fonts, spacing, and surface styling, not positions.

## What is actually functional

- All 11 row types are projected from semantic YAML into registered MCEL panel/field/action components, including rows that currently lack live data sources.
- The actual MCEL registry serializes **153 component instances** and refuses invalid parent–child combinations. The browser mounts all registered instances.
- A keyboard-operable view strip, client-side search, selectable rows, inspection panel, and responsive resizing work with labeled synthetic fixture observations.
- Consequential actions appear disabled and never reach production services. Advanced/operational controls are collapsed by default.
- The generated MCEL `layout-tree.v1` successfully round-trips through the official DSL compiler without implicit source mutation.
- Chromium verifies 320/390/768/1440/1920px layout and interactions; resizing across breakpoints is independently exercised.

## Explicit non-guarantees

- Registered MCEL component serialization and generated DOM interactions do **not** establish external data producer integrations or full toolkit component runtime certification.
- The current generated rows present **synthetic observations**, not real Task Manager process, worker, or AI events. The search is over fixtures.
- The current layout planner searches a **bounded, existing four/six-option grammar**, not arbitrary layout geometry. It should be tested against independent holdout applications before generality is claimed.
- Human aesthetic acceptance is **unverified**. Browser geometry validity does not by itself certify visual quality.
- No authorization, real consequential effect, production migration or rollback has been demonstrated.

The next vertical slice should connect one read-only observation source to these generated components, then validate semantic/event provenance and visual acceptance before any live cutover.

## Full-page visual inspection (shared with the earlier Frob)

The `--mode semantic` path calls the **same** screenshot/HTML-gallery utility as
`--mode sweep`. Both capture `full_page=True` PNGs, with hashes and pixel
sizes. On successful browser verification the semantic output contains:

- `gallery.html` — offline visual inspection gallery with viewport/view/state filters;
  thumbnails link to original full-resolution PNGs.
- `visual-manifest.json` — one provenance-checked record per PNG, including
  viewport, view, synthetic state, dimensions, SHA-256 and browser observations.
- `visual-review-template.csv` — blank reviewer judgments (no automatic claim
  that the interface looks good).
- `preview-320.png`, `preview-390.png`, `preview-768.png`, `preview-1440.png`,
  and `preview-1920.png` — existing names, now **full-page** default captures.
- Additional `visual-<width>-<view>-<state>.png` captures for every declared
  view on desktop/mobile, selected row/inspector, search with zero matches,
  expanded optional controls, crowded rows, long text, and synthetic
  operation-pending/failure examples.

Stress and operation images are explicitly fixture simulations, **not** proof
of live producers or backend effect execution. Both the screenshot manifest and
report retain human visual quality as *unreviewed*. The generic visual utility
is implemented in `tools/mcel_frob_visual_evidence.py` and shared by the older
layout-engine Frob, rather than introducing a second screenshot/gallery tool.

Open `runtime/reports/mcel-layout-frob/semantic-generation/gallery.html` after
running `python tools/mcel_layout_frob.py --mode semantic`. The gallery and PNGs
can be opened without a web server. Do not open a stale gallery without also
checking its manifest/source provenance.
