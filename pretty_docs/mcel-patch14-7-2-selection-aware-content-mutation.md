# MCEL Patch 14.7.2 — Universal Selection-Aware Content Mutation

**Status:** offline laboratory engineering acceptance. **Requires Patch 14.7.1.** Read-only Task Manager verification; no production route promotion or operational effects.

## Design invariant

Selection is a user-held relationship to **specific evidence**. A live render must never silently substitute new text under an existing native browser selection. But selecting one field must not freeze an entire process card, floating overlay, or surrounding collection.

The reusable `McelSelectionContinuity` service implements `mcel.selection-aware-content-mutation.v1`, independent of process, file, and worker semantics. Consumers identify fields using stable `data-mcel-selection-key` markers, scoped to their semantic record identity with `data-mcel-selection-scope`. Selection observation belongs to the document, while content mutation remains element-owned.

The service provides:

- `service(document).observe(callback)` to subscribe to native selection changes and release listeners on teardown;
- `overlaps(document, node)` to identify the DOM content actually selected (including cross-field ranges);
- `mutate(surface, fresh)` to reconcile content in place, updating unaffected text immediately and retaining only selected changed text;
- `mutateText(element, nextText)` for ordinary collection-row field values;
- `capture(root)` / `restore(root, anchor)` to reconstruct an identical selection after a necessary remount, using semantic keys, field offsets, direction, exact selected text, and record-scope identity. Restoration refuses changed or ambiguous evidence.

## Interaction behavior

- Updating unselected measurements, source evidence, and field labels does not unmount a selected value's text node.
- If a value **under selection** changes, that particular value is temporarily retained. The content surface displays a notice, while the other fields and the authoritative source record continue updating. Row-level retained text is likewise labeled as older selected data.
- Copying selected text with the browser retains the selected earlier evidence. The explicit **Copy complete source record** control copies the latest authoritative record, not the older selected text.
- After selection is released, pending fields catch up to the most recent observed value. Intermediate samples are intentionally coalesced.
- A verified process generation allows in-place continuity. A disappearing or generation-changed record is **not** granted selection continuity merely because an identifier resembles the old one.
- The canonical collection validator rejects stale row-field displays unless a native, non-collapsed selection *actually intersects* the retained field, and its retained text and current expected projection match the supplied pending metadata. A flag alone cannot bypass source validation.
- Inline and floating details use the same generic mutation mechanism and preserve existing interactive controls, disclosure state, and semantic identity.

## Verification

```powershell
python -m tools.mcel_patch14_7_2_selection_continuity_frob
python -m pytest -q tests/test_mcel_patch14_7_2_selection_continuity.py tests/test_mcel_patch14_7_1_integrity.py tests/test_mcel_patch14_7_capability_negotiation.py tests/test_mcel_patch14_6_promise_verification.py tests/test_mcel_patch14_5_human_first.py
```

Runs actual read-only Task Manager semantic snapshots under Chromium at **390px and 1440px**, **normal and stressed** data. It verifies selected value stability, unselected live updates, multiple coalesced telemetry updates, native selection, latest-record copying, row-field retention and validator acceptance, floating-card selection continuity, a cross-field range, semantic selection-anchor restoration, and refusal when selected evidence or record identity changes.

The report is written to `runtime/reports/mcel-patch14-7-2-selection-continuity/report.json`.

## Scope boundaries

This patch supplies a **generic, reusable contract**, wired to the opted-in semantic collection and its expandable/floating detail views. Other MCEL widgets can adopt it by registering semantic leaf keys and using the service; this patch does **not** claim every production widget is already integrated. The single-document native Selection API is in scope; cross-document iframes, shadow-root selection, rich-text editor composition/IME, drag-and-drop and asynchronous cross-origin selections require separate adapters and acceptance tests. A selection spanning multiple semantic roots is protected during individual mutations, but remount restoration currently requires both endpoints inside the same identity scope. Layout reorganization while active selection spans distinct roots remains a future integration test. The patch does not establish superior human usability or promote the read-only Task Manager lab.
