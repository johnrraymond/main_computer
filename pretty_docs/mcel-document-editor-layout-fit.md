# Document Editor layout fit

Patch 21 stabilized the Document Editor as a usable MCEL surface, not only a
semantic one. Patch 43 parks the app at runtime-baseline until its requirements
contract and semantic adapter coverage are truth-auditable. The current
snapshot still uses the Patch 22b-era three-lane interface; the Patch 24a
outline, modal-picker, and docked-companion material below remains a target
specification rather than live-state documentation.

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

For conformance-required app surfaces, content-fit violations remain hard
runtime visual-fit failures even when an app is parked at runtime-baseline.

## Patch 22a and 22b page auto-fit

The three-lane layout kept the right AI rail on the right, kept the left Pretty
Docs rail shrink-first, and auto-fit the document page down inside the remaining
center lane. Page fit refresh observes the canvas, object stage, shell, and app
visibility so a reopened app does not keep stale zoom.

## Patch 24a target lane responsibilities

Patch 24a changes the specification before changing the live UI.

The target conceptual layout is:

```text
left Document Outline | editable document page | right Document AI dock
```

These are conceptual zones, not three columns that must always consume width.
The center document is the permanent primary surface. The outline and companion
may stay visible, dock, collapse, use drawers, or overlay according to owned
container capacity.

The Pretty Docs file list is no longer a persistent lane. It moves to a
transient modal opened by `Open Pretty Doc...`.

### Independent scroll ownership (Patch 24a1)

The workbench uses bounded height and three independent vertical scroll owners:

```text
outline list       -> scrolls heading navigation only
document viewport  -> scrolls authored pages only
companion content  -> scrolls AI conversation/results only
```

Document scrolling MUST NOT move either side panel. The outline rail and right
companion remain anchored in the Document Editor viewport while authored pages
move through the center document viewport.

The outline list owns its own vertical scrollbar. As document caret, selection,
or viewport position changes, the corresponding outline entry becomes active
and the outline scroll position adjusts just enough to keep that active
navigation point visible. This synchronization MUST NOT change document scroll
position. Clicking an outline entry explicitly scrolls/focuses the document.

The companion header, close/reopen control, and prompt composer remain
reachable. Only the companion conversation/result body scrolls when its content
overflows. Document scroll events do not change the companion's vertical
position.

The editor shell is not the document scroll owner. Toolbars, side surfaces, and
modal chrome remain outside the authored-page scroll. A Pretty Docs modal owns a
separate candidate-list scrollbar.

At narrow widths the outline and companion may become drawers or overlays, but
their scroll ownership remains independent from the document.

### Viewport containment correction (Patch 24a2)

The active Document Editor route owns a definite browser-viewport height. Its
outer stage, canvas wrapper, app root, and document shell MUST clip overflow and
pass the remaining height to the internal grid.

Long outline or file-list content MUST NOT increase the height of the editor
shell, the application stage, or the browser page. Overflow belongs to the
bounded navigation list, authored document viewport, or companion content
scroll owner.

Responsive rules MUST NOT impose a positive minimum height on the companion
that can force the shared workbench row beyond the available viewport height.


### Bounded navigation row sizing correction (Patch 24a3)

A bounded navigation list MUST overflow as complete readable rows. The grid or
list layout MUST NOT compress repeated rows until their title tracks have zero
height. Each temporary file-list row and future outline row keeps an intrinsic
or explicit minimum block size; excess rows belong to the navigation
scrollbar.

### Left outline lane

The navigation lane is owned by the current document outline. Its content is
the heading hierarchy of the loaded document.

The outline lane:

- remains anchored while the center document scrolls;
- owns a scrollbar for long heading lists;
- keeps the active heading visible by adjusting only its own scroll position;
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
width while inactive. When expanded or active, it remains vertically anchored
while the document scrolls; its internal conversation/result body is the only
companion subregion that should require vertical scrolling.

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

### Target runtime conformance additions

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
document scrolling does not move the outline or companion
outline, document, and companion content have independent scroll owners
active outline entry stays visible without changing document scroll position
companion header and prompt composer remain reachable during internal overflow
```

## Current implementation boundary

Patch 24a is specification and contract-test work only in this snapshot. The
live Document Editor remains on the Patch 22b-era UI. A future implementation
of the outline, modal picker, and docked companion must update the live markup,
surface contract, layout checks, and chronology together.

### Focused host and vertical budget correction (Patch 24a4)

Code Editor and Document Editor use the focused applications host. The host
reserves the collapsed Apps gutter before calculating the app-owned viewport.
The collapsed Apps control MUST NOT overlap the Explorer, document outline,
authoring page, companion, or any other app-owned control. Expanding the Apps
launcher is an explicit temporary overlay.

For Document Editor, permanent vertical chrome is limited to:

```text
compact document/session row
compact formatting row
primary workbench
compact status strip
```

The generic embedded-widget ticker is not part of the Document Editor layout.
Full-screen remains available from the document-owned session row.

The session row and formatting row MUST remain single-line scroll/overflow
owners rather than wrapping into extra vertical rows. The compact status strip
MAY expose save state, revision, reload, and discard controls, but it MUST NOT
consume the primary workbench's remaining-height allocation.

The companion has exactly three vertical owners:

```text
anchored header
scrollable context/conversation/result body
anchored prompt composer
```

Anchor controls and quick actions belong inside the scrollable middle body.
They MUST NOT collapse that body to zero height while their readable descendants
remain painted outside it.

The focused host and app shell must satisfy these measurable properties:

```text
app left edge >= reserved Apps gutter right edge
app height == focused canvas height
primary workbench row uses minmax(0, 1fr)
document primary host height >= 320px at the supported short viewport
document scroll does not move either side panel
```

