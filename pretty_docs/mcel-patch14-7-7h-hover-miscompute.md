# MCEL Patch 14.7.7h — Hover Miscompute and Immediate Handoff

Trace under examination: L18-01 `mcel.hover-pointer-trace.v2` 2026-10-30 18:27:08–18:27:52 UTC; viewport 1272×588. This patch applies after 14.7.7g.

## Observed failure

The actual horizontal width was correctly 469.8px against a 324px list, and the external scrollbar had zero lateral gap. The errors were in the vertical and temporal geometry:

1. An out-of-view record 202 opened at y=-276px in a 588px viewport; its initial 856px planned card was revised to 631px, and the external rail was hidden. This is a stale hover/open transaction, not a valid geometry choice. A stale hover timer must not open a row whose hit-test origin has disappeared from the scroll view, and the host also must refuse the request independently.
2. A CSS `height:481.3125px` set in JavaScript was returned as `481.312px` by the browser. The follow loop compared strings and re-applied height on subsequent scroll events even though the intended geometry was unchanged.
3. On parent scroll handoff, the list's anchor moved by 100px during the wheel event, but the promoted card was not repositioned until a later animation frame. The recorder logged up to 100px temporary anchor/row difference. For host-mediated wheel handoff, the card and its external rail must follow in the same event.

## Implementation

- The gallery's hover activation requires a fresh pixel hit-test of the **same original row** inside the scroll view and browser viewport. Legacy non-row-hover paths retain their existing behavior.
- `openUnclip` refuses a row that is completely outside the visible scroll view. It never invents a replacement location.
- The follow loop compares numeric CSS lengths with a 0.01px tolerance to ignore CSSOM fractional-pixel serialization noise.
- The scroll-handoff host synchronizes the original row and external rail after transferring scrolling to the list, before returning from the wheel event. Ordinary scroll and document follow logic remains unchanged.

No change to the ~145%-width L18-01 test fixture, original grid columns, existing field identities, or configured 15% overscroll threshold.

## Verification

`python -m tools.mcel_hover_miscompute_frob` — PASS for 390, 1100, 1272, including a direct offscreen host API negative test and synchronous handoff alignment. The same test fails on 14.7.7g without these corrections.

Also PASS: `tools.mcel_hover_wide_unclip_frob`, `tools.mcel_hover_anchor_order_frob`, `tools.mcel_hover_live_geometry_frob`, and `tools.mcel_hover_pointer_plugin_frob`.

These offline Chromium fixtures do not certify all live Windows scroll gestures, touch input, legacy lifecycle retirement tests, or whole application performance. No changes made to `docs/` or other documentation roots.
