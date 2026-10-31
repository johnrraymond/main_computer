# Patch 14.7.7g — L18-01 wide hover, beyond the scroll view

## Purpose

The L18-01 operator gallery previously rendered each record at the exact width of its scroll collection. Its hover tests proved vertical unclipping but did not exercise horizontal escape from that collection.

This patch makes the **same original record** expand horizontally as well as vertically **only in the L18-01 operator-gallery wide-hover test**. The list stays at its original width, and no clone/second card or horizontal list scrollbar is introduced. This is not a universal policy change for unrelated applications. The independent orthogonal L18 matrix remains unchanged.

## Layout invariants

- The source row's pre-hover grid columns are frozen at their measured widths; original field rectangles do not change.
- The actual original row opens as the same top-layer element. Only the **new** information region has additional horizontal space.
- The hover width targets 145% of the collection width, bounded by the available browser width and room for the exterior scrollbar. At very constrained viewports, viewport fit takes precedence over reaching the target ratio.
- The invisible layout anchor retains its original width and flex order, so sibling positions remain unchanged.
- The scrollbar is positioned at the **expanded card's** right edge, not the anchor's original right edge. Both the hover interaction region and follow/scroll repair use the expanded width.
- Source telemetry updates, wheel scrolling, and the existing 15%-overscroll handoff continue to use the original row and the same scroll owner.

## New browser regression

`python -m tools.mcel_hover_wide_unclip_frob`

At 390, 1100, and 1272 CSS-pixel viewport widths, the real L18-01 fixture verifies all of the following:

- wide card exceeds the scroll view by more than 10 px, without changing the list's width or introducing horizontal list overflow;
- the exact original row is promoted, with original field positions and dimensions preserved;
- card, anchor, and exterior scrollbar remain correctly aligned;
- the newly exposed horizontal band is part of the hover pointer region, including across the one-second leave grace;
- a live synthetic source update does not collapse or reflow the card;
- card-wheel scrolling still belongs to the original row;
- no browser script exceptions.

The existing `mcel_hover_anchor_order_frob` and `mcel_hover_live_geometry_frob` remain relevant and were rerun. They separately verify nonzero list scroll, anchor tracking, external scrollbar hit testing, telemetry identity, and scroll handoff.

## Limitations

These are offline Chromium fixtures driven by the actual MCEL lab scripts. They are not a guarantee of correct behavior in an operator's running browser or every responsive layout. The L18-01 gallery is deliberately wide-hover-specific, while other production MCEL consumers retain their existing geometry policy.
