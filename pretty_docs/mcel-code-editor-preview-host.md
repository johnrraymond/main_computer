# MCEL Code Editor Preview Host

`mcel-code-editor-preview-host.js` is a thin Code Editor host for the reusable MCEL surface preview pathway.

It does not define MCEL semantics and it does not parse authored surfaces itself. It reads the selected editor text, delegates authored-surface analysis to `McelAuthoredSurfaceDocument`, delegates preview rendering and verification to `McelSurfacePreviewContract`, and displays a bounded preview status only when the selected file actually contains MCEL surface ridges.

```text
selected file text
  -> McelAuthoredSurfaceDocument.analyzeText(...)
  -> SemanticSurfaceIR + SharedLayoutGrammar
  -> McelSurfacePreviewContract.renderPreview(...)
  -> renderer-interface verification
  -> surface extractor
  -> round-trip verification
  -> compact Code Editor preview status
```

Normal files stay visually unchanged: the `MCEL Preview` chip remains hidden when the selected file is not an authored MCEL surface.

The preview popover is lazy-opened, non-modal, and bounded inside the title-bar action area. It does not replace Monaco and it does not mutate authored content.

## API

```javascript
McelCodeEditorPreviewHost.renderPreviewForSource({sourceText})
McelCodeEditorPreviewHost.createPreviewRenderer()
McelCodeEditorPreviewHost.refresh()
McelCodeEditorPreviewHost.togglePreview()
```

The default renderer is a conservative debug HTML projection. It emits the same semantic and layout ridges required by the MCEL preview contract and is verified by round-trip extraction before the status is marked `PASS`.

## Boundary

The Code Editor owns only:

```text
reading selected text
hosting a bounded status chip
hosting a lazy preview popover
```

MCEL owns:

```text
authored document analysis
SemanticSurfaceIR
SharedLayoutGrammar
renderer contract
surface extraction
round-trip verification
```
