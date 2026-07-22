# MCEL Authored Surface Document

`mcel.authored-surface-document.v1` analyzes authored text without depending on a specific host.

It classifies HTML, SVG, JSON ridge records, JSON surface bundles, and plain text. Documents with no MCEL ridges are `not-applicable` rather than failures.

The main API is:

```javascript
McelAuthoredSurfaceDocument.analyzeText(sourceText)
```

The analyzer uses the existing MCEL surface pathway:

```text
authored text
  -> surface ridges
  -> SemanticSurfaceIR
  -> SharedLayoutGrammar
  -> diagnostics
```

This module is reusable by the Code Editor, MCEL Lab, Website Builder, tests, and future visual editors.
