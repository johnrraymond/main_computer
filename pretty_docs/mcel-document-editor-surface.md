# MCEL Document Editor Surface Pilot

Patch 20 promoted Document Editor from a layout-bound legacy app into a
semantic-runtime MCEL app surface.

The original intent was narrow:

```text
existing Document Editor UI
  + document semantic ridges
  + layout ridges
  + runtime diagnosis contract
  -> extractable SemanticSurfaceIR
  -> valid SharedLayoutGrammar
  -> enrolled semantic-runtime conformance
```

Patch 20 did not redesign Document Editor or add a visible panel. Patch 24a now
specifies the next Document Editor interaction model before Patch 24b changes
the live UI.

## Current surface

```text
document-editor.surface.primary
```

## Current application-surface regions

```text
document-editor.region.navigation
document-editor.region.menu
document-editor.region.toolbar
document-editor.region.status
document-editor.region.primary
document-editor.region.advanced
document-editor.region.document-page
document-editor.region.document-content
document-editor.region.companion
```

## Patch 24a target model

Document Editor has two related semantic layers:

```text
application surface
  shell, menu, toolbar, outline rail, authoring lane, companion, status, modal

authored document
  document, page, section, heading, block, paragraph, object,
  selection, annotation, export target
```

The application surface hosts and projects the authored document. The authored
document is not flattened into an unbounded list of static application-layout
nodes.

The editor has three conceptual zones:

```text
left navigation  -> headings for the current document
center primary   -> editable document/page
right companion  -> Document AI context and actions
```

Only the center primary zone is the permanent width and document-scroll owner.
The outline and companion are anchored auxiliary surfaces. Depending on owned
container capacity, they may remain visible, compact, dock, or overlay without
forcing the document below its readable fit.

File selection is a transient task:

```text
Open Pretty Doc
  -> modal file picker
  -> choose a candidate
  -> resolve unsaved changes when required
  -> load the selected document
  -> rebuild the current document outline
```

### Scroll ownership and viewport anchoring

Document Editor MUST NOT use the authored page scroll as a shared workbench
scroll. The editor body has bounded viewport height and equivalent independent
scroll owners for:

```text
outline-scroll-container
document-scroll-container
companion-content-scroll-container
```

The contract is:

- The center document scrollbar moves authored pages only.
- Document scrolling MUST NOT move the outline rail, the companion panel, or
  the document/format toolbars vertically.
- The outline body owns its own vertical scrollbar for long heading lists.
- Document caret/selection/viewport changes update the active outline entry.
- The outline scroll position SHOULD adjust as needed to keep the active entry
  visible without changing the document scroll position.
- Activating an outline entry is the explicit action that scrolls/focuses the
  matching authored heading.
- The companion remains anchored while the document scrolls. Only its internal
  conversation/result body scrolls when companion content overflows; its
  heading, reopen/close affordance, and prompt composer remain reachable.
- A file-picker modal owns its own candidate-list scroll and does not borrow the
  document scrollbar.

At constrained widths an outline drawer or companion overlay remains an
independent anchored surface. Overlay presentation does not transfer document
scroll ownership to the shell.

### Left navigation: current document outline

The left rail MUST represent the structure of the currently loaded document. It
MUST NOT use its persistent body as the Pretty Docs file list.

The outline contract is:

- Extract heading entries from the current editable document.
- Preserve heading level and hierarchy.
- Use stable authored-node identity so duplicate heading text remains
  unambiguous.
- Rebuild after document load and update after edits with bounded/debounced
  work.
- Scroll to and focus the matching authored heading when an outline entry is
  activated.
- Track the heading nearest the current caret, selection, or viewport position.
- Highlight that active heading and auto-scroll the outline's own list just
  enough to keep the active navigation point visible.
- Remain anchored in the editor viewport while the document pages scroll.
- Show a clear `No headings yet` empty state.
- Keep a compact current-document label and an `Open Pretty Doc...` command
  available without embedding the file list in the rail.

Planned semantic identifiers:

```text
document-editor.region.navigation
  role: document-outline-navigation

document-editor.node.document-outline
document-editor.node.current-document
document-editor.control.open-pretty-doc
document-editor.control.outline-heading
document-editor.edge.outline-navigates-heading
```

Individual heading entries are authored-document projections. They SHOULD be
identified by stable authored heading IDs and MUST NOT become permanent static
application-layout nodes.

### Pretty Docs modal picker

The Pretty Docs list belongs in an accessible modal opened by the explicit
`Open Pretty Doc...` action.

The modal MUST:

- expose searchable/selectable Pretty Docs candidates;
- show enough identity to distinguish similarly named documents;
- support pointer and keyboard selection;
- provide explicit `Open` and `Cancel` actions;
- trap focus while open and restore focus to the invoking control when closed;
- avoid silently discarding local edits;
- keep the picker open and report the error if the selected document cannot be
  loaded.

When the current document is dirty, opening another document MUST pass through
an explicit save/discard/cancel decision. Selecting a candidate alone does not
mutate the current editing session.

Planned semantic identifiers:

```text
document-editor.region.file-picker-modal
document-editor.node.pretty-doc-picker
document-editor.node.pretty-doc-candidate
document-editor.control.confirm-open-pretty-doc
document-editor.control.cancel-pretty-doc-picker
document-editor.edge.picker-selects-document
```

Candidate rows are dynamic modal content. They MAY expose semantic identity and
accessible controls, but MUST NOT be emitted as unbounded static
application-surface layout nodes.

### Right companion: docked when inactive

The Document AI companion remains owned by the right side. It SHOULD reclaim
space for the primary authoring lane when it is not actively being used.

Supported states:

```text
docked
expanded
active
overlay
```

The companion is active while any of these conditions hold:

- keyboard focus is inside the companion;
- an AI request is running;
- the prompt contains an unsent draft;
- generated output is awaiting review or apply;
- the user has pinned the companion open.

When none of those conditions hold, the companion MAY slide or compact to a
right-side dock. Docking MUST preserve the thread, draft, result, and selection
context. A compact, readable control MUST remain available to reopen it.

The expanded or active companion remains anchored to the right edge of the
Document Editor viewport while the document scrolls. Document scrolling MUST
NOT translate the companion with authored pages. Companion overflow is handled
inside the companion content body so its header and prompt composer remain
reachable.

At narrow owned widths, the companion MAY use an explicit overlay policy rather
than permanently reducing the document page below its usable fit. Overlay mode
must remain dismissible and must not silently cover active document controls.

Planned fit/state ridges:

```text
data-mcel-companion-state="docked|expanded|active|overlay"
data-mcel-fit-policy="collapse-optional"
data-mcel-fit-role="document-ai-companion"
```

## Current static nodes

Until Patch 24b implements the target model, the current runtime surface still
extracts these Patch 20 nodes:

```text
document-editor.node.document-session
document-editor.node.selected-document
document-editor.node.document-library
document-editor.node.layout-state
document-editor.node.export-target
document-editor.node.document-page
document-editor.node.document-content
document-editor.node.document-block
document-editor.node.selected-object
document-editor.node.ai-context
document-editor.node.status-message
```

## Current static edges

```text
document-editor.edge.library-selects-document
document-editor.edge.session-owns-page
document-editor.edge.page-owns-content
document-editor.edge.content-contains-block
document-editor.edge.content-targets-object
document-editor.edge.toolbar-configures-layout
document-editor.edge.companion-describes-content
document-editor.edge.export-projects-document
```

## Current controls

```text
document-editor.control.toggle-library
document-editor.control.toggle-ai
document-editor.control.insert-scene
document-editor.control.export-pdf
document-editor.control.format-bold
document-editor.control.layout-apply
document-editor.control.reload-disk
document-editor.control.discard-draft
document-editor.control.ai-apply
document-editor.control.ai-send
```

These current identifiers remain documented so Patch 24b can make deliberate
create/replace decisions rather than silently claiming the target state is
already live.

## Runtime helper

`mcel-document-editor-surface.js` exposes:

```javascript
McelDocumentEditorSurface.buildStaticSurfaceRidgeRecords()
McelDocumentEditorSurface.applyStaticSurfaceRidges(document)
McelDocumentEditorSurface.extractCurrentSurface(document)
```

The helper is defensive. If the MCEL module is absent, Document Editor continues
to behave as before. Static attributes are also present in `document.html` so the
surface can be extracted without requiring live app boot.

## Registry policy

Document Editor is enrolled as:

```text
surface-aware
semantic-runtime
```

It therefore requires all five app-surface conformance layers:

```text
semantic-surface
layout-grammar
runtime-ownership
runtime-visual-fit
diagnostic-no-throw
```

## Patch boundary

Patch 24a is specification and contract-test work only. It does not claim that
the live Document Editor already provides the outline, modal picker, or docked
companion. Patch 24b is responsible for implementing those behaviors and
updating the runtime surface identifiers.

## Safety rules

- Do not silently discard authored changes.
- Do not confuse file selection with current-document navigation.
- Do not use one shared vertical scroll for the outline, document, and
  companion.
- Do not move either side panel when authored pages scroll.
- Do not destroy companion state when docking it.
- Do not emit unbounded dynamic heading or file rows as static layout grammar.
- Required readable/control content must retain a declared fit policy.
- Keep the application surface and authored-document semantics distinguishable.
- No unrelated domain vocabulary.
