# MCEL File Explorer Layout Fit Probe

This patch closes the gap between the File Explorer semantic surface pilot and visible runtime fit.

The pilot originally proved that File Explorer exposed MCEL ridges, could build a `SemanticSurfaceIR`, and could build a `SharedLayoutGrammar`. That did not prove that the visible root rail fit in the actual app viewport.

This document records the additional contract:

- File Explorer root buttons must wrap long Windows paths instead of clipping them.
- File Explorer shell columns must stay bounded at medium widths.
- The details panel may move below the roots/list columns before it forces horizontal clipping.
- Runtime diagnostics must treat File Explorer panes, root buttons, entries, and preview text as visual integrity candidates.

This remains a surface-fit patch, not a behavior rewrite. It does not add panels or change file browsing semantics.


## Diagnostic reliability follow-up

The visual-fit probe must never turn a layout measurement bug into a blind
`diagnosis-threw` report. File Explorer visual-fit checks now use a bounded
`clippedPaintBox` helper before comparing descendants to sibling paint boxes.
That helper clips a measured box to overflow-owning ancestors or an explicit
owner boundary, then returns a bounded measurement instead of allowing an
undefined helper or raw scroll box to crash diagnosis.

Going forward, the File Explorer surface contract has two separate checks:

- readable content must wrap or fit inside its visual owner;
- diagnostic probes must keep reporting measurements even when a specific
  visual-fit candidate is awkward or browser-repaired.


## Range-fragment clipping follow-up

Readable text probes also measure `Range#getClientRects()` fragments. Those
range fragments must be clipped through the same bounded helper path as element
paint boxes. The dedicated `clippedRangeBox` wrapper keeps the visual-fit probe
from crashing when it compares text fragments instead of whole elements.
