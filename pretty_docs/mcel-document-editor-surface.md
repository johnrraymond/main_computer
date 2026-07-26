# MCEL Document Editor Surface Pilot

Patch 20 promotes Document Editor from a layout-bound legacy app into a
semantic-runtime MCEL app surface.

The intent is narrow:

```text
existing Document Editor UI
  + document semantic ridges
  + layout ridges
  + runtime diagnosis contract
  -> extractable SemanticSurfaceIR
  -> valid SharedLayoutGrammar
  -> enrolled semantic-runtime conformance
```

This does not redesign Document Editor and does not add a visible panel.

## Surface

```text
document-editor.surface.primary
```

## Regions

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

## Static nodes

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

## Static edges

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

## Controls

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

## Safety rules

- No visible UI redesign.
- No app behavior refactor.
- No new editor panel.
- No unrelated domain vocabulary.
- MCEL adds semantic structure, runtime diagnosis contract, layout records, and
  conformance enrollment.
