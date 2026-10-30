# MCEL Patch 14.7.6.1 — Real Row Ownership of Unclipped Hover

## Failure

Patch 14.7.6 reproduced the source row in a separate `document.body` overlay and added the expanded information as the overlay's sibling of that reproduction. Although it matched the original pixels, it was **not a descendant of the original hoverable row**. The pointer could consequently leave the original element while appearing to remain inside its expanded information; a delayed dismissal could follow. The original geometry Frob did not test holding a real pointer over the extended information longer than the dismissal timer.

## Correction

`McelPresentationHosts.createOverlayHost().openUnclip()` now appends a single fixed-position **unclipped extension** as a child of the real `mcel-row`, not as a separate body-level clone. The actual `mcel-row` is preserved; no mirror exists, no original fields are recreated, and the element's selection/focus/click state remains authoritative. The extension participates in the browser's native DOM hover hierarchy: `row.matches(':hover')` remains true when the pointer moves from the source row into its extension or its exterior scrollbar. The shared 1-second dismissal timer runs only after the pointer leaves the logical row subtree, and reentry cancels it.

The extension is out of layout flow using `position:fixed`, positioned edge-to-edge with the original row, and responds to scroll/resize changes by following source geometry. The scrollbar remains an external accessory and the 15% scroll-handoff policy is unchanged. The extension has a strictly validated auxiliary semantic presentation slot (`unclip-extension`) under the original row; the registered semantic collection validator recognizes it and the tree authority delegates its subordinate controls to that validator. Active inline expansion may coexist with the authorized extension without falsifying source membership.

When a visible record retires, its attached extension's `sourceRowId` follows the retired presentation identity. Retirement, selection-continuity, and ID-replacement tests remain required regressions.

## Real-browser acceptance

```powershell
python -m tools.mcel_hover_unclip_scroll_handoff_frob
python -m pytest -q tests/test_mcel_hover_shared_hit_region.py
```

The new real-pointer test opens hover normally, holds it stationary until after activation, moves the pointer into the extension and remains for **1.5 seconds**, verifies the original row is still `:hover` and its extension remains connected, then exits and verifies the timer delays dismissal for approximately one second. The 390px and 1440px browser tests verify direct DOM ownership, original row geometry, no clone, content selection, exterior scrollbar, parent-scroll attachment, and independent scroll-handoff control. The screenshot comparison evaluates the **actual row before and after**, rather than comparing it against a clone. It reports only tiny raster differences after eliminating border overlap.

## Limitations

This is a read-only opt-in laboratory presentation. Fixed descendants escape ordinary scroll overflow, but some browser styling features (e.g. a transformed containing block) can change the containing block or clipping behavior; such hosts require capability negotiation or an alternate painting strategy. This patch fixes interaction ownership but does not claim arbitrary ancestor-clipping escape under every CSS configuration or perfect touch/trackpad momentum classification. It does not promote the behavior to production Task Manager.
