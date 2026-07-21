# MCEL Surface Extractors

Contract: `mcel.surface-extractors.v1`

Patch 05 adds the extraction side of the MCEL semantic-surface pathway.

The extractor layer reads rendered HTML or SVG and recovers:

- MCEL ridge records
- `SemanticSurfaceIR`
- shared layout grammar
- validation diagnostics

This is the first step toward the rule:

```text
A rendered surface is valid MCEL output only if MCEL semantics can be recovered from it.
```

## Public API

```javascript
McelSurfaceExtractors.extractSurfaceBundleFromHtml(htmlText, options)
McelSurfaceExtractors.extractSurfaceBundleFromSvg(svgText, options)

McelSurfaceExtractors.extractSemanticSurfaceFromHtml(htmlText, options)
McelSurfaceExtractors.extractSemanticSurfaceFromSvg(svgText, options)

McelSurfaceExtractors.extractLayoutGrammarFromHtml(htmlText, options)
McelSurfaceExtractors.extractLayoutGrammarFromSvg(svgText, options)

McelSurfaceExtractors.extractRidgeRecordsFromMarkup(markup, options)
McelSurfaceExtractors.canonicalExtractedSurfaceFingerprint(markup, options)
```

## Surface selection

Rendered output may contain chrome, hidden proof panes, diagnostics, or multiple surfaces.
The extractor chooses the surface in this order:

1. `options.surfaceId`
2. exactly one `data-mcel-authoritative="true"` surface
3. a single available `data-mcel-surface-id`
4. diagnostic failure when selection is ambiguous

This prevents hidden/non-authoritative diagnostic surfaces from corrupting the authoritative extracted surface.

## Required ridges

The extractor consumes the MCEL ridges defined by Patch 01, including:

```text
data-mcel-surface-id
data-mcel-surface-kind
data-mcel-node-id
data-mcel-node-type
data-mcel-edge-id
data-mcel-edge-kind
data-mcel-from
data-mcel-to
data-mcel-relation
data-mcel-region
data-mcel-control
data-mcel-source
data-mcel-provenance
data-mcel-home-region
data-mcel-actual-region
data-mcel-teleported
```

It also consumes the layout ridges defined by Patch 04:

```text
data-layout-anchor-x
data-layout-anchor-y
data-layout-width
data-layout-height
data-layout-z
data-layout-region
data-layout-route-kind
data-layout-from-port
data-layout-to-port
```

## Optional extraction hints

The extractor can infer viewport and region bounds from rendered output:

```text
data-layout-viewport-width
data-layout-viewport-height
data-layout-safe-margin

data-layout-x
data-layout-y
data-layout-width
data-layout-height
```

For SVG region elements it can also read `x`, `y`, `width`, and `height`.

## Non-goals

This patch does not add renderers.
This patch does not add editor UI panels.
This patch does not add any domain-specific vocabulary.

It only makes rendered HTML/SVG surfaces mechanically extractable back into MCEL structures.
