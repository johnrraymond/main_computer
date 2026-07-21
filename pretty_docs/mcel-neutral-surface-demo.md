# MCEL neutral surface demo

Contract: `mcel.neutral-surface-demo.v1`

This module is the first small end-to-end MCEL surface fixture. It is intentionally domain-neutral.

It proves the full pathway:

```text
SemanticSurfaceIR
  + SharedLayoutGrammar
  + renderer interface
    -> HTML projection
    -> SVG projection
    -> surface extractors
    -> round-trip verification
    -> HTML/SVG agreement
```

## Surface

The demo surface contains only generic authoring concepts:

```text
region.workbench
Observation.A
Hypothesis.B
EDGE.observation-supports-hypothesis
trace_evidence
```

The module does not define application vocabulary. It does not define editor UI behavior. It does not add production application screens.

## Renderers

The demo exports two renderer implementations:

```javascript
McelNeutralSurfaceDemo.htmlRenderer()
McelNeutralSurfaceDemo.svgRenderer()
```

Both accept the same `SemanticSurfaceIR` and `SharedLayoutGrammar`. Both emit semantic ridges and layout ridges. Both identify their renderer profile with `data-mcel-renderer` and projection with `data-mcel-projection`.

## Verification

The main helper is:

```javascript
McelNeutralSurfaceDemo.verifyNeutralDemoRoundTrip()
```

It renders HTML and SVG, verifies each projection through `McelSurfaceRoundTrip`, and verifies that the two extracted projections agree.

This is a fixture for the MCEL surface pathway, not a visual application.
