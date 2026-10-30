# MCEL Patch 14.7.7 — Atomic Unclip of Individual Hovered Records

**Scope:** The explicitly opted-in MCEL lifecycle L18 gallery and the reusable original-row `openUnclip` presentation host. No changes to the operational Task Manager or its source processes. The lab contains 18 independently mounted cases with 20 source records each.

## Corrected ownership

A `mcel-collection` is a scroll/clipping host, **not** the automatic hover target. Every `mcel-row` is an independent element. The hovered **original row** enters the browser's native top layer, with an empty layout anchor left in its source collection. Its original DOM children, identity, event handlers, text selection, and focused source content are not cloned or substituted. Other rows remain in the collection's original scrollport, which does not enlarge or acquire a new hover presentation.

L18 opts in using `rowHoverUnclip: true`. `collectionHoverUnclip: true` is accepted as a legacy alias for consumers of the former option, but now selects correct individual-record hover. `createCollectionUnclip` remains exposed as a compatibility API; the L18 gallery no longer invokes it. Existing non-opted-in consumers retain their current presentation-policy behavior.

## Two-phase presentation

1. Before showing a popover, append the independent extension as an **initially hidden, absolutely positioned descendant** of the original record. This adds no list row and causes no reflow. Measure full extension contents, row geometry, viewport space, and external scrollbar needs. Choose above/below placement, extension scrollport height and width, scrollbar side, thumb length, and relative coordinates.
2. Apply that complete geometry while still hidden, then synchronously promote the **original row** into the native top layer and expose its already-prepared extension. No show–recalculate–clip–widen sequence. The source row width/height and previously visible pixels remain fixed. Only the extension scrolls internally. The scrollbar uses a separate exterior 12px track and does not subtract width from the original row.
3. While exposed, follow the empty anchor when the list or page scrolls. Scroll handling does not renegotiate element dimensions every animation frame. Viewport resize or an actual change in extension content triggers a separate measured geometry transaction.

`createScrollHandoff` retains its configurable 15% overscroll default and reversal logic. The momentum guard remains a heuristic rather than a guaranteed way to distinguish deliberate trackpad scrolls from inertia.

## Verified continuity and retirement

After a layout-authority reconciliation, inline grid styles can be regenerated. This previously cleared the promoted row's fixed coordinates, making it jump to `(0,0)` and lose hover-protected retirement. The row host now restores the existing negotiated coordinates and dimensions **after** the layout commit and observes later style resets. This does not claim continuity for unknown generations: in L18-01 unverified source observations may legitimately replace the original row; in L18-02 verified generation retains the same DOM element and the open hover presentation.

## Tests and boundaries

Run `python -m tools.mcel_row_atomic_hover_20_frob`. It verifies both 390px and 1100px constrained-viewport cases, including 18×20 distinct row owners, first-frame scrollbar visibility and geometry over approximately 30 animation frames, screenshot-level preservation of the original row, mouse dwell and dismissal, source/sibling identity, extension scroll ownership, 15% handoff, identity-aware source updates, and all 18 explicit hover requests under allowed/denied host capabilities. It produces a JSON report and screenshots under `runtime/reports/mcel-row-atomic-hover-20/`.

The previous `python -m tools.mcel_collection_unclip_20_frob` command now forwards to the corrected row-hover gate, rather than asserting that the full collection must expand.

Also run the existing `mcel_hover_unclip_scroll_handoff_frob`, `mcel_patch14_7_2_selection_continuity_frob`, `mcel_patch14_7_4_lifecycle_integrity_frob` and `mcel_lifecycle_taguchi_acid_frob` regressions. The browser results use an offline Chromium semantic fixture; they do not certify arbitrary other browser engines, actual OS Task Manager data, or pathological cross-origin/transform clipping hierarchies. The goal is an observable correct mechanism, not a claim of perfect browser-independent pixel identity.
