# MCEL Code Editor Surface Diagnostics

`mcel.code-editor-surface-diagnostics.v1` connects the live Code Editor diagnosis report to the MCEL surface pathway.

It is domain-neutral. It does not define app-specific content. It converts the Code Editor's measured authoring surface into:

```text
diagnosis report
  -> MCEL ridge records
  -> SemanticSurfaceIR
  -> SharedLayoutGrammar
  -> diagnostic HTML projection
  -> round-trip extraction check
```

## Why this exists

Earlier patches added the pieces independently:

```text
semantic ridges
SemanticSurfaceIR
shared layout grammar
surface extractors
round-trip verification
renderer interface
neutral demo surface
```

This patch wires those pieces into the real Code Editor self-diagnosis path. The Code Editor can now report whether its primary authoring surface is not only visible and owned, but also representable as a recoverable MCEL semantic surface.

## Public API

```javascript
McelCodeEditorSurfaceDiagnostics.buildCodeEditorSurfaceRidgeRecords(report)
McelCodeEditorSurfaceDiagnostics.buildCodeEditorSurfaceModel(report)
McelCodeEditorSurfaceDiagnostics.renderDiagnosticSurfaceHtml(surfaceIR, layoutGrammar)
McelCodeEditorSurfaceDiagnostics.evaluateCodeEditorSurfacePathway(report)
McelCodeEditorSurfaceDiagnostics.summarizeForDiagnosis(report)
```

## Summary fields

The self-diagnosis report receives:

```json
{
  "mcelSurfacePathway": {
    "semanticRidgesPresent": true,
    "surfaceIrBuildable": true,
    "surfaceIrValid": true,
    "layoutGrammarPresent": true,
    "layoutGrammarValid": true,
    "extractable": true,
    "roundTripStatus": "pass"
  }
}
```

The same summary is also copied to:

```text
report.summary.mcelSurfacePathway
```

## Failure shape

Failures are predicate-specific. For example, if the measured Monaco editor is not visible or has a zero layout box, the pathway reports:

```text
code-editor-primary-editor-not-layout-usable
```

The pathway does not replace the existing Code Editor contract checks. It adds MCEL surface-level evidence to the same diagnostic report.
