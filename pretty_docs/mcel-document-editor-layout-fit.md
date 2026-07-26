# Document Editor layout fit

Patch 21 stabilizes the Document Editor as a usable MCEL surface, not only a semantic one.

Patch 21a corrects the lane policy after live use showed that stacking the right Document AI companion was the wrong degradation. The intended behavior is:

```text
left Pretty Docs library | document page | right Document AI companion
```

The right rail remains on the right. The left file list is the lane that shrinks first, because it is navigational support rather than the primary authoring surface or the companion/action rail.

The observed failure class has two parts:

- the component could exceed its available width even though the browser viewport was not narrow;
- floating menu/debug actions and a display-contents workspace wrapper could be misclassified by diagnostics as required-region or visual-overlap failures.

The fix is deliberately narrow:

- keep the existing Document Editor UI and behavior;
- keep the `[e/w/g]` diagnostics counter in the Document Editor header;
- make the document shell respond to its own container width;
- preserve the right Document AI rail as the right grid column at constrained widths;
- shrink the left Pretty Docs library lane through compact widths and ellipsis instead of moving the AI rail below the page;
- avoid treating floating menu bodies as normal semantic layout projections;
- treat the visible document shell as the required workspace box because `.document-workspace` is a `display: contents` grouping wrapper;
- mark Document Editor panes, controls, and readable text as runtime visual-fit candidates without making the entire action header a readable text range.

Going forward, Document Editor conformance requires the same layers as File Explorer:

```text
semantic-surface
layout-grammar
runtime-ownership
runtime-visual-fit
diagnostic-no-throw
```

The important distinction is that semantic validity only proves what the surface means. Runtime visual-fit proves that the user can actually read and use the surface in the rendered app.

## Patch 21b residual visual-fit hardening

Patch 21b addresses the remaining "good-ish" state after the three-lane policy landed:

```text
left library | document page | right Document AI
```

The intended layout remains three lanes. The right rail stays on the right, and the Pretty Docs/library lane continues to be the lane that compresses first.

The remaining fixes are diagnostic and fit-oriented:

```text
- library cards use border-box sizing so a shrunk file list does not bleed by its own padding
- MCEL no longer treats large document containers as readable text leaves
- open menu bodies and debug menu bodies remain floating controls, not normal semantic projections
- the page overlay layer is ignored by sibling-overlap checks because it is aria-hidden and pointer-passive
- display: contents wrappers are ignored by collapsed-owner detection
```

This keeps the runtime-visual-fit layer focused on actual unreadable/clipped content instead of intentional overlays or structural wrappers.


## Patch 21c rail content-fit contract

Patch 21c fixes the remaining Pretty Docs header clipping case. The three-lane policy was correct, but the left rail was allowed to become narrower than its own header controls could fit:

```text
PRETTY DOCS | Refresh | Close
```

The correction adds a micro-layout contract inside the shrink-first rail:

```text
- the rail remains the shrink-first column
- the right Document AI rail remains on the right
- the library header is a compact grid, not a single non-wrapping row
- the title can wrap within the rail
- header actions can wrap into compact rows
- at very narrow rail widths, Refresh and Close become icon-sized controls with aria-label/title text
- truncation/compact-icon behavior is declared with data-mcel-fit-policy
```

This captures the distinction between macro fit and micro fit:

```text
macro fit: the big regions fit in the app shell
micro fit: the readable/control content inside each region still has a declared fit policy
```

For semantic-runtime apps, content-fit violations are now hard runtime visual-fit failures. A semantic surface should not show a green top-level diagnostic result while required readable/control content is visibly clipped.
