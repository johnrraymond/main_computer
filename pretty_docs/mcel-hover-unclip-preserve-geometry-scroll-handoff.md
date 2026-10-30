# MCEL hover extension and threshold-controlled scroll handoff

Patch 14.7.6 — universal presentation-host behavior. Lab opt-in, no production Task Manager promotion.

## Visual continuity

The default `onHover` candidate remains an externally portaled presentation but advertises two new capabilities: `overlay.unclip-preserve-geometry` and `scroll.threshold-handoff`. Its original portion is a computed-style replica of the **existing visible row** placed at the same viewport coordinates with the same width and height. No source row is removed or resized and no list reflow is requested. An independently styled, richer **append-only extension** follows the original. The existing `floating-over-source` presentation remains available as a fallback if a host declines unclipping. Direct `request(id,'onHover')` remains synchronous for test and keyboard callers; native pointer hover now waits a configurable 1000ms activation dwell, and leaving both the source and exposed presentation schedules dismissal after a configurable 1000ms. Return to either region cancels the timer. Native text selection and focused controls protect against accidental dismissal.

The original live DOM row remains in the canonical list as the semantic authority. The unclipped row is a read-only visual replica; clicking it requests selection of the actual logical element, and live changes refresh the replica except while the person is selecting its text. The replica's selection keys are separately namespaced so they cannot be confused with keys in the appended inspector. The inspector includes copy actions and retains the existing 14.7.2 selection continuity contract. The host follows the source's screen coordinates through scroll and resize, instead of clamping the preserved region elsewhere. Screen-edge fallback can place only the extension *above* the preserved row if there is insufficient space beneath; its original pixels are never repositioned.

A narrow exterior scrollbar track lives immediately to the **right** of the content; a lane is reserved in negotiated collection layout **before hover**, so displaying that track never takes space from the existing row. If the right side cannot fit, the track may be placed outside the left boundary. The extension has its own independent scrollport, with keyboard scrolling, scrollbar thumb dragging, and no interior scrollbar width subtraction. The preserved original area remains fixed while the extension scrolls. If neither side can fit a 12px exterior track, the host exposes `data-external-track-placement="unavailable"` rather than silently stealing the row's width. Its wheel region still works.

## Scroll ownership

The universal `McelPresentationHosts.createScrollHandoff()` defaults to `handoffThresholdPercent:15` of extension-visible-height and `momentumGuardMs:140`. The event is consumed by the extension while it can move. At a boundary, additional requested scroll distance is accumulated against the threshold. When satisfied, scroll ownership transfers to the containing scroll host. Subsequent deltas in that direction go directly to the parent until direction reverses. A reversal immediately resets pending transfer and restores ownership to the extension. `deltaMode` line/page units are converted into pixel-equivalent distance. A short boundary guard reduces (but cannot prove absence of) momentum-triggered transfers; browsers do not provide a reliable standardized distinction between user intention and inertial wheel tails. Touch drag and browser-native scrollbar behavior remain independent; this patch's threshold contract applies to wheel/trackpad events.

The parent host changes its own scroll offset directly; it does not synthesize untrusted wheel events. At its boundary it forwards the unconsumed remainder to another real scrollable ancestor. The overlay listens to host/page scroll events and follows the source pixel-for-pixel; scrolling the extension itself does not scroll the parent before handoff.

## Verification and limits

```
python -m tools.mcel_hover_unclip_scroll_handoff_frob
python -m pytest -q tests/test_mcel_hover_unclip_scroll_handoff.py
```

The offline Chromium Frob compares screenshot pixels for the source and preserved replica at 390/1440px in normal/stressed Task Manager semantic fixtures, verifies unchanged row geometry, scroll source tracking, native text selection and copyability, scrollbar outside original width, and a deterministic 15% parent-scroll handoff/reversal. The existing 14.7.2 selection, 14.7.3 retirement, and 14.7.4 lifecycle tests remain regression gates. The older 14.7 baseline Frob has been updated to assert the **new intentional hover behavior** (dwell and follow-source scroll), not the superseded fade-on-scroll behavior. The 14.7.4 Frob explicitly scrolls a source into view before moving the pointer to it; an offscreen source cannot be physically hovered without the user first navigating to it.

Full, immutable screenshot pixel equality cannot be guaranteed by a DOM clone in every browser, font, and rendering mode (especially generated pseudo-elements, canvases, and ancestor-dependent styling). The test sets a bounded pixel difference and rejects geometry changes. Future work should examine transferable original-node rendering where semantic layout validation and native selection can be preserved. This patch is not a cross-browser accessibility certification or proof of intent classification for inertial trackpad gestures.
