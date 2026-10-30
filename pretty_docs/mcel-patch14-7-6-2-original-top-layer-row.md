# Patch 14.7.6.2 — Original DOM Row Top-Layer Unclipping

## Corrected invariant

Hover does **not** create a substitute for an existing MCEL row. The exact original `mcel-row` node is promoted into the browser's native **popover top layer** (manual mode), keeping its event handlers, fields, text selection, identity, and DOM parent. An empty, hidden geometry anchor reserves its former layout footprint in the scroll container; it contains no information and is not an alternate row. The extension is a child of the real promoted row. Scrolling positions the original row relative to its anchor.

The previous 14.7.6.1 positioned only the newly created extension beyond clipping. It never removed the source row itself from the scroll container's clip boundary. That behavior was fundamentally inconsistent with the requested unclip contract.

## Browser support and failure policy

`showPopover()`/`hidePopover()` promote the original row outside ordinary scroll clipping. If the top-layer API is unavailable or promotion fails, the capability is refused rather than silently creating a lookalike row. Negotiation may select another declared mode. No normal Task Manager actions are enabled in this lab.

The row retains its original computed padding and grid geometry; the top-layer style only changes placement, box-model constraints and paint layer. When closed, the row returns to normal layout, and its empty anchor is removed. The layout validator admits anchors only if they are empty and paired to the actual original source row.

The element's internal extension scrollport, external scrollbar, percent-threshold scroll handoff, shared hover boundary, delayed dismissal, and selection protection remain in effect.

## Critical lifecycle correction

Source reconciliations ignore anchors when determining DOM record order; otherwise they would reinsert an unchanged row and collapse a native selection. When a live record retires, the anchor remains paired with the retained row's new presentation identity, so layout checks do not reject retirement under hover.

## Acceptance tests

```powershell
python -m tools.mcel_hover_unclip_scroll_handoff_frob
python -m pytest -q tests/test_mcel_original_row_top_layer.py
```

Tests use real Chromium pointer movement and check that: the original row (object identity) is hit-tested in the browser's top layer, `:hover` remains set across its extension, native text selection survives a verified-generation update, source and anchor move together on container scrolling, screenshot geometry stays constant, and the top layer/anchor are fully released on dismissal. Existing 14.7.2 selection, 14.7.3 retirement, and 14.7.4 lifecycle regressions must also pass.

## Remaining boundaries

This is a Chromium-verified approach; other browser engines and exotic CSS containing/scroll hosts are not certified. An active text selection across a genuine source identity replacement may be invalidated deliberately. The empty anchor is not a virtualizer host implementation.
