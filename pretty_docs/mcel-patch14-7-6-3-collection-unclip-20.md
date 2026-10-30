# MCEL 14.7.6.3 — original collection unclipping with 20-record L18 acid tests

## Root cause

Earlier patches promoted an individual `mcel-row` and appended independently rendered details, while the source `mcel-collection` remained scroll-clipped. Both the collection and individual rows then had delayed hover controllers, allowing an obsolete record popup to appear on pointer exit. Merely cancelling DOM pointer events could not reliably cancel already scheduled row timers.

## Corrected presentation ownership

- The existing `mcel-collection` *itself* is promoted through the browser's native `showPopover()` top layer. It is never cloned or reparented; its 20 rows are the same DOM nodes, with the same field layout and handlers.
- An invisible, empty sibling anchor reserves the collection's original layout footprint. The original collection tracks that anchor while either the containing page or list is scrolled. Its original width and top-left coordinates remain fixed for all initially visible content.
- No per-row automatic hover listeners are installed when `collectionHoverUnclip` is enabled. The old card cannot be armed, including on exit. This capability is an explicit laboratory opt-in; existing non-opted consumers keep their original presentation contract.
- One-second pointer hover activates the exposed source. A one-second grace interval after leaving the source and external track dismisses it, except for a protected native text selection.
- Real details are appended **after all source rows** in the same collection. The CSS `order` is explicitly greater than all row ordering indices to prevent the extension from appearing between original rows.
- The internal scrollport does not consume original content width for a scrollbar. The accessory track is rendered separately in the top layer, immediately outside the original content edge, and uses the original collection's actual `scrollTop`.
- `createScrollHandoff` owns wheel inputs inside the exposed collection. After a configured fraction (default 15%) of the visible scrollport is traversed beyond its boundary, the real scrollable ancestor receives subsequent deltas; reversing direction restores child ownership. Momentum discrimination is heuristic.
- Source observations briefly suspend the top-layer styling synchronously, reconcile the authoritative records, and re-expose the *same collection* before the next paint. The collection's scroll position is retained. The collection and tree validators recognize only the declared extension slot, rejecting arbitrary extra pseudo-rows.

## Test expansion

The flat L18 gallery mounts 20 unique semantic process records for **each** of the 18 independently interactive cases, including source identity, generation, varying process state, availability, measurement, role, and command values. This is offline, generated test data; none of the records imply actual machine processes. The focused workbench and orthogonal automated screening continue using their original two-record and ten-record stress fixtures to preserve their baseline test meaning. Gallery cases still support updates, retirement, selection, hover, copy, fault injection, live telemetry, and 10/15/20/30% handoff configuration.

## Verification

```powershell
python -m tools.mcel_collection_unclip_20_frob
pytest -q tests/test_mcel_collection_unclip_20.py tests/test_mcel_lifecycle_flat_gallery.py
```

The new browser Frob asserts 360 unique mounted rows across 18 cases, original collection top-layer ownership, unchanged initial pixel region, appended extension ordering, external scrollbar, no legacy row popup, live source updates while hovered, interior wheel consumption, overscroll transfer to page, and one-second exit cleanup, at two viewport widths.

## Scope

This does not enable collection-owned hover for production Task Manager. Use `collectionHoverUnclip:true` on an opted-in universal semantic collection. It does not claim cross-browser certification or perfect distinction between deliberate input and trackpad inertia.
