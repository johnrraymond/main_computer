# MCEL representation-to-layout: Patch 2.1

Patch 2.1 strengthens the **experimental** Patch 2 Frob. The production MCEL
application layout authority and Website Builder are deliberately unchanged.

## Run

From the repository root:

```text
python tools/mcel_representation_layout_frob.py
python -m pytest -q tests/test_mcel_representation_layout_contract.py tests/test_mcel_representation_layout_patch21.py
python -m webbrowser runtime/reports/mcel-representation-layout/gallery.html
```

The default command tests **6 independent collections × 5 widths × 2 modes**:
320, 390, 560, 640 and 1100 pixels, shrinker enabled and disabled. A browser
must actually run for a PASS; `--no-browser` explicitly yields `UNVERIFIED`.

Both the original source fixture and the separate adversarial flat file are
retained. No database, daemon, production mutation or reference generation is
required. Additional cases are in
`tools/fixtures/mcel_representation_layout_adversarial.json`.

## New cases and fail-closed assertions

- **Long identifiers:** full, unbroken path-like original values must fit the
  320px inspector using wrapping, not scrolling, clipping or ellipsis. Every
  inspected paragraph's scroll width and glyph bounding box is checked in the
  actual browser. Inspector tests run in both compression modes.
- **Near collisions:** every pair of different original identifiers must have
  different visible representations. Any collision in the projection is refused.
- **Missing fields:** truly absent and explicitly empty values remain distinct
  in source identity and are explicitly labeled `Not provided` versus `Empty`;
  the exact inspector states `[not provided]` or `[empty]` rather than
  manufacturing the JavaScript string `undefined`.
- **Competing required fields:** five simultaneously required fields must
  remain visible after contextual compression, including their labels. The
  geometry check refuses clipped content rather than silently accepting it.
- **Responsive transitions:** 560 → 390 → 320 → 1100 → 560 pixel live resize
  runs on the original and two adversarial collections without remounting them.
- **Negative controls:** keep the original 560/640px shrinker-disabled failures;
  additionally force `white-space:nowrap` on the exact inspector to demonstrate
  that the new detector rejects a regression and passes after repair.

The output remains at `runtime/reports/mcel-representation-layout/` with
`report.json`, before/after HTML/JSON/PNG, `gallery.html`, and the zipped
review archive. `report.json` now uses schema
`mcel.representation-layout-frob.v2` and names every independent dataset and
its measured PASS/FAIL evidence.

## Limits

This certifies **only** registered-element experimental collection behavior.
It is not evidence of production MCEL wiring, arbitrary datasets, arbitrary
viewport widths, assistive-technology quality or human comprehensibility.
The Frob will refuse the tested cases when required text or inspector originals
clip, but an impossible layout in an untested dataset is not magically repaired.
