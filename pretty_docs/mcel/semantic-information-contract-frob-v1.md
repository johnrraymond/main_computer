# MCEL Semantic Information Contract + Information Quality Frob v1

A **frob-only** integration of the existing flat-file text-shrinker adapter with the
registered `element.core.collection`, `element.core.collection-row`, and
`element.core.field` MCEL component contracts. No production renderer or
application-package changes; no database or model inference.

## Run

```text
python tools/mcel_information_quality_frob.py
python -m pytest -q tests/test_mcel_semantic_information_contract.py tests/test_mcel_text_shrinker_adapter.py
python -m webbrowser runtime/reports/mcel-information-quality/gallery.html
```

`--no-browser` writes the complete semantic projections and HTML but labels the
Frob UNVERIFIED. `--chromium-executable PATH` can use a specified browser binary.

The existing `tools/fixtures/mcel_text_shrinker_widgets.json` contains both the
putative reference value sets and per-field semantic `presentation.text_shrinker`
opt-in. Only opted-in fields may change their *display* strings.

The Frob renders two read-only synthetic MCEL collections (a Downloads-like
file list and an unrelated inventory collection) at 320, 390 and 1100 pixels.
It captures before/after PNGs and checks real browser behavior:

- Source row identities, typed values and semantic field identities are retained.
- Sorting is calculated from the original values, especially byte sizes.
- Searching can find substrings removed from the on-screen abbreviation.
- Selection exposes every original field value via the inspector.
- Required Size is visible and there is no document-level horizontal overflow.
- Opt-out does not compress anything; opt-in saves rendered characters in each
  of the two independent data domains.

Result: `runtime/reports/mcel-information-quality/information-quality-review.zip`.

This demonstrates wiring the adapter into *registered MCEL components*, not
certification of an installed application or an aesthetic-quality judgment.
Pixel cost is currently approximate and viewports use a simple responsive
experimental shell. Later integration should reuse the same semantic opt-in
contract in actual application bindings without introducing another database.
