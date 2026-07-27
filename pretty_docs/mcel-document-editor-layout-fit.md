# Document Editor layout fit

Patch 21 stabilized the Document Editor as a usable MCEL surface, not only a
semantic one.

Patch 21a corrected the lane policy after live use showed that stacking the
right Document AI companion was the wrong degradation. The then-current layout
was:

```text
left Pretty Docs library | document page | right Document AI companion
```

The right rail remained on the right. The left file list was the lane that
shrunk first.

The observed failure class had two parts:

- the component could exceed its available width even though the browser
  viewport was not narrow;
- floating menu/debug actions and a display-contents workspace wrapper could be
  misclassified by diagnostics as required-region or visual-overlap failures.

The correction was deliberately narrow:

- keep the existing Document Editor UI and behavior;
- keep the `[e/w/g]` diagnostics counter in the Document Editor header;
- make the document shell respond to its own container width;
- preserve the right Document AI rail as the right grid column at constrained
  widths;
- shrink the left Pretty Docs library lane through compact widths and ellipsis
  instead of moving the AI rail below the page;
- avoid treating floating menu bodies as normal semantic layout projections;
- treat the visible document shell as the required workspace box because
  `.document-workspace` is a `display: contents` grouping wrapper;
- mark Document Editor panes, controls, and readable text as runtime visual-fit
  candidates without making the entire action header a readable text range.

Document Editor conformance requires:

```text
semantic-surface
layout-grammar
runtime-ownership
runtime-visual-fit
diagnostic-no-throw
```

Semantic validity proves what the surface means. Runtime visual-fit proves that
the user can actually read and use the rendered surface.

## Patch 21b residual visual-fit hardening

Patch 21b kept the three-lane layout and fixed diagnostic/fit noise:

```text
- library cards use border-box sizing
- MCEL does not treat large document containers as readable text leaves
- open menu bodies and debug menu bodies remain floating controls
- the page overlay layer is ignored by sibling-overlap checks
- display: contents wrappers are ignored by collapsed-owner detection
```

## Patch 21c rail content-fit contract

Patch 21c fixed the Pretty Docs header clipping case:

```text
PRETTY DOCS | Refresh | Close
```

The rail remained shrink-first, but its controls gained declared wrap,
compact, and compact-icon behavior.

This established the distinction:

```text
macro fit: the big regions fit in the app shell
micro fit: readable/control content inside each region has a fit policy
```

For semantic-runtime apps, content-fit violations are hard runtime visual-fit
failures.

## Patch 22a and 22b page auto-fit

The three-lane layout kept the right AI rail on the right, kept the left Pretty
Docs rail shrink-first, and auto-fit the document page down inside the remaining
center lane. Page fit refresh observes the canvas, object stage, shell, and app
visibility so a reopened app does not keep stale zoom.

## Patch 24a target lane responsibilities

Patch 24a changes the specification before changing the live UI.

The target layout is:

```text
left Document Outline | editable document page | right Document AI dock
```

The Pretty Docs file list is no longer a persistent lane. It moves to a
transient modal opened by `Open Pretty Doc...`.

### Left outline lane

The navigation lane is owned by the current document outline. Its content is
the heading hierarchy of the loaded document.

The outline lane:

- MAY compact at constrained widths;
- MUST keep heading navigation readable and keyboard reachable;
- MAY truncate individual long heading labels while preserving accessible full
  text;
- MUST provide a `No headings yet` state;
- MUST NOT fall back to displaying the Pretty Docs file list.

The outline is a dynamic projection of authored headings. Its container belongs
to static app layout grammar; an unbounded list of heading rows does not.

### Center authoring lane

The center lane remains the primary width owner. It reclaims width whenever the
right companion docks.

Page auto-fit must respond to all of these changes:

```text
outline width change
companion expanded/docked transition
modal close after document load
application visibility change
owned container resize
```

The modal itself must not alter the saved document zoom. After a successful
load, the new document is fit against the current center-lane capacity.

### Right companion lane

The companion remains right-owned but no longer reserves its full expanded
width while inactive.

States:

```text
docked   -> compact right-side affordance; document lane reclaims width
expanded -> full companion is visible
active   -> focus, request, draft, pending result, or pinned-open state
overlay  -> narrow-capacity presentation above the page
```

The companion may dock only when it has no active condition. Docking preserves
thread, draft, result, and selection context. The reopen control uses a declared
compact/compact-icon fit policy.

The target fit policy is:

```text
expanded/active: normal right companion lane
docked: collapse-optional with reachable compact control
overlay: explicit overlay policy at narrow owned width
```

The companion must not auto-dock during a running request, while its input has
an unsent draft, while generated output awaits review/apply, while focus remains
inside it, or while it is pinned open.

### Pretty Docs modal

The file picker is a temporary overlay, not a fourth persistent lane.

It must:

- fit inside the owned application bounds;
- trap focus and restore it on close;
- keep `Open` and `Cancel` reachable;
- support a scroll owner for long file lists;
- avoid projecting every candidate row into static layout grammar;
- pass through save/discard/cancel when current edits are dirty;
- remain open with an actionable error when loading fails.

### Runtime conformance additions for Patch 24b

The implementation patch should extend runtime checks to prove:

```text
navigation role is document-outline-navigation
persistent navigation does not contain the Pretty Docs file list
file picker is modal and transient
docked companion leaves a reachable reopen control
docked companion state preserves context
center lane grows when the companion docks
page auto-fit refreshes after companion transitions and document load
dynamic outline/file rows do not pollute static layout collision evidence
```

## Patch boundary

Patch 24a is specification and contract-test work only. The live Document
Editor remains on the Patch 22b UI until Patch 24b implements the outline,
modal picker, and docked companion.
