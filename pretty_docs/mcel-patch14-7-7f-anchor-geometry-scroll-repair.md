# MCEL Patch 14.7.7f — ordered anchor and stable hover scroll geometry

This patch follows Patch 14.7.7e. It is based on the browser trace captured on 2026-10-30 at a 1272 × 588 viewport and the corresponding repository snapshot.

## Root cause and evidence

The negotiated continuity layout uses a flex-column collection and assigns each original `mcel-row` a numeric CSS `order`. `openUnclip()` inserted a plain `<div>` anchor before the row but did not carry over the row's order. The anchor therefore occupied an early flex position, whereas the row occupied its ordered slot. In the trace for process 1128, the row opened at y=199.94px and its layout anchor was already at y=-476px. The overlay's follow routine then moved the original card to y=-476px. Because the plan used that offscreen anchor for vertical space, it incorrectly concluded the 994px content fit and disabled the external rail. Browser scrolling then transferred to the parent list rather than scrolling the expanded card.

The trace also records a content-height feedback loop: repeated content refreshes enlarged the explicit grid track by 8px per replan even without a corresponding change to the actual details. The old size calculation was reading `pane.scrollHeight`, which is affected by its own allocated grid track and its height-100% child.

Separately, the gallery installed a document-scroll listener that forcibly closed all preview popovers when the page moved, even by 1px, although the row overlay host already follows window/document scroll through the real layout anchor.

## Corrections

- Copy the original row's computed CSS flex `order` onto its invisible layout anchor before insertion. This preserves the exact ordered slot and original sibling layout.
- Measure the additional content's intrinsic `scrollHeight`, not the explicitly sized extension grid track. Correct the pre-show measurement synchronously after native popover promotion and before exposing the extension.
- Remove the gallery's unconditional `page-scrolled` dismissal for a live row hover. The owner continues to follow page/list scroll and preserves the existing pointer-leave grace and scroll-handoff policy.
- Preserve all earlier patches' original-row DOM identity, external 12px rail, plugin diagnostics, no-remount live telemetry, 1s hover/leave timers and 15% scroll threshold. No structural clone or separate details scrollport is introduced.

## New regression gate

Run `python -m tools.mcel_hover_anchor_order_frob`. It checks nonfirst-row `order` and anchor geometry, first-frame scrollbar, content-height stability across telemetry, 1px document scroll continuity, row wheel priority, and boundary-wheel handoff to the parent list at desktop/narrow viewports.

## Validation / limits

The new focused gate passed at 390px and 1100px. Existing live-geometry and pointer-plugin gates also passed at both widths. The legacy motion-diagnostics Frob stalled in this environment and was not counted as passing. The wider MCEL lifecycle regression suite is not declared cleared. Validation is in an offline Chromium fixture based on MCEL Lab, not a complete certification of every live Windows interaction.
