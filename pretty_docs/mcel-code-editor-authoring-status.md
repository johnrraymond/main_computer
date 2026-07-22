# Code Editor MCEL Authored Surface Status

`mcel-code-editor-authoring-status.js` is a thin host integration over `McelAuthoredSurfaceDocument`.

It reads the current selected-file text and shows a compact title-bar chip only when the document contains MCEL surface ridges.

The chip is hidden for normal source files so the Code Editor layout remains unchanged for ordinary editing.

States:

```text
hidden        no MCEL authored surface detected
PASS          authored surface builds SemanticSurfaceIR and SharedLayoutGrammar
WARN          authored surface is valid but has warnings
FAIL          authored surface has errors
```

This patch keeps the editor as a host. The reusable logic stays in MCEL.
