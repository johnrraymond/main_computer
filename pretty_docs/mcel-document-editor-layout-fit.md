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
