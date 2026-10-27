# MCEL text-shrinker flat-file adapter experiment

Scope: **one JSON flat file**, a per-field semantic opt-in, an adapter to the existing `mcel_information_budget.evaluate`, a one-command smoke/preview. There is **no SQLite, observer, watcher, training, production registration, or policy change**.

## Run

```text
python tools/mcel_text_shrinker_smoke.py
python tools/mcel_text_shrinker_adapter.py --widget downloads --viewport 1100
python tools/mcel_text_shrinker_adapter.py --widget inventory --viewport 390
python -m unittest discover -s tests -p test_mcel_text_shrinker_adapter.py
```

Open `runtime/reports/mcel-text-shrinker/downloads-1100.html` to judge the results. Generated JSON files map each `display` back to `raw` and identify which flat-file context was used.

## Edit putative values

The only data source is `tools/fixtures/mcel_text_shrinker_widgets.json`:

- `value_sets`: putative values relevant to each semantic field; they are *examples*, not learned facts
- `widgets`: synthetic rows plus column semantics, including `presentation.text_shrinker`
- `enabled: false` or a missing setting leaves a field's original string untouched
- `enabled: true`, `mode: contextual_identifier`, `values_ref: ...` enables context-based identifier display
- `enabled: true`, `mode: adaptive_size`, `values_ref: ...` enables numeric size units and context-based precision

The current contextual heuristic strips only literal whole-segment prefixes and suffixes common to every referenced example, and only for an opted-in field. It does **not** infer arbitrary human intent or train a model. The existing width-budget compressor handles any remaining long strings. Full original strings and original numbers remain available for inspection, sorting and machine operations. When the reference set is heterogeneous, the conservative heuristic may strip nothing: that is a finding to test with real human judgments.

The adapter call is `adapt_widget(widget_semantics_and_rows, value_sets, viewport_px=1100)` and returns an explicit, machine-readable presentation projection. A production renderer could optionally consume that projection later, but **this patch does not wire the production renderer**. No edits to existing app packages, application startup, or quality Frob.
