# MCEL 14.7.7b: one continuous hover card

This is a corrective patch on top of 14.7.7 (individual-row hover) and 14.7.7a (L18-01-only gallery). It does **not** restore the other seventeen operator examples or change Task Manager behavior by default.

## Contract

- The original `mcel-row`, and no cloned substitute, enters the top layer. The `mcel-collection` remains its 20-record scrolling host.
- Original grid tracks are measured and retained in order. A final explicit grid track is added for the extension. `grid-row` is explicit rather than auto-placed, because multi-column/dense grids can otherwise backfill the extension among existing fields.
- The original row **increases in height**. The extension is a normal-flow grid child, not an absolutely positioned floating card; existing `mcel-field` nodes are never wrapped, replaced, or moved.
- The row's original visible content keeps its screen coordinates, and its former bottom-border pixel is retained as an internal seam. The original row's eventual outer border is at the new bottom.
- A formerly translucent hover background is composited against original ancestor backgrounds before the row enters the top layer, preventing underlying sibling rows from showing through the now-larger original card. This compositing uses flat ancestor background colors; image/gradient backdrops require further host negotiation.
- The host measures and prepares original geometry, appended content, and an external scrollbar **before** `showPopover` for the row; one synchronous operation commits presentation. The scrollbar is outside the original row's right edge (or left if required) even when the extension spans fewer grid columns.
- The existing configurable hover activation/dismissal and 15%-default scroll ownership handoff are unchanged.
- Selection continuity, identity-qualified source updates, and retirement protection remain active.

## Verification

Run `python -m tools.mcel_row_atomic_hover_20_frob` against the offline Chromium fixture. This now requires a growing original row, unchanged original `mcel-field` rectangles, an in-flow extension, opaque original card background, stable original pixels, stable first-frame geometry, scrollbar placement and handoff, real pointer retention, and the continued 20-record L18-01 example.

The legacy `tools.mcel_hover_unclip_scroll_handoff_frob` now compares *only the original pixel region* while requiring the original card to actually grow. Three corner pixels in one desktop variation can show a strong border-color delta; the gate reports the exact large-difference fraction rather than concealing it.

These are offline Chromium tests. A Windows browser integration check is still required. The old whole-collection hover implementation remains in the codebase for compatibility but is **not** used by the L18 gallery's automatic hover path.
