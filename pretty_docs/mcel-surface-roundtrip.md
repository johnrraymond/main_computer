# MCEL surface round-trip verification

`mcel.surface-roundtrip.v1` is the MCEL contract for checking rendered semantic surfaces.

It connects the already-separate pieces:

```text
SemanticSurfaceIR
SharedLayoutGrammar
Rendered HTML/SVG
SurfaceExtractors
RoundTrip verification
```

## Boundary

This contract is domain-neutral.

It does not define domain vocabulary.
It does not define a renderer.
It does not define editor chrome.
It does not style a surface.

It only answers whether rendered output still preserves the expected MCEL meaning and the expected shared layout grammar.

## Core rule

```text
A rendered surface is valid MCEL output only when its semantic graph and layout grammar can be extracted and compared to the canonical expected models.
```

## Main API

```javascript
McelSurfaceRoundTrip.verifyRenderedSurfaceRoundTrip({
  expectedSurfaceIr,
  expectedLayoutGrammar,
  renderedText,
  surfaceKind: "html" // or "svg"
});
```

The verifier extracts a surface bundle from the rendered text, validates the recovered `SemanticSurfaceIR` and `SharedLayoutGrammar`, then compares both to the expected canonical versions.

```javascript
McelSurfaceRoundTrip.verifyHtmlAndSvgAgree(htmlText, svgText);
```

This checks sibling projections. HTML and SVG may use different renderers, but they must recover the same surface graph and the same layout grammar.

## Comparison scope

Semantic comparison includes:

```text
surface id
surface kind
surface role
surface contract
nodes
edges
regions
controls
```

It intentionally ignores renderer/projection labels because sibling projections are allowed to differ there.

Layout comparison includes:

```text
viewport
regions
node anchors
node sizes
node regions
edge routes
edge ports
control bounds
layout policy
```

## Diagnostics

Failures are structured diagnostics. Examples:

```text
semantic-surface-roundtrip-mismatch
layout-grammar-roundtrip-mismatch
roundtrip-unsupported-surface-kind
roundtrip-missing-surface-extractor-api
```

Each mismatch diagnostic includes a small list of first-difference paths and the expected/extracted fingerprints.

## Position in the MCEL path

```text
MCEL ridges
  -> SemanticSurfaceIR
  -> SharedLayoutGrammar
  -> rendered surface
  -> SurfaceExtractors
  -> RoundTrip verification
```

This is the safety loop that later renderer and editor patches build on.
