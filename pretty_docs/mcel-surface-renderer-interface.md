# MCEL surface renderer interface

Contract: `mcel.surface-renderer-interface.v1`

This patch defines the neutral renderer boundary for the MCEL semantic surface pathway.

A renderer is allowed to choose a visual skin, but it must not invent or discard meaning. The renderer contract is:

```text
SemanticSurfaceIR
  + SharedLayoutGrammar
    -> renderer profile
    -> rendered HTML/SVG text
    -> MCEL surface extractors
    -> round-trip verification
```

## Renderer profile

A renderer profile declares:

```text
id
version
supported surface kinds
default surface kind
capabilities
required inputs
required output ridge behavior
```

The profile must be stable because rendered output must identify the profile that produced it with `data-mcel-renderer`.

## Renderer implementation

A renderer implementation exposes:

```javascript
{
  profile: {
    id: "renderer.neutral-html",
    surfaceKinds: ["html"],
    defaultSurfaceKind: "html"
  },
  render(request) {
    return renderedText;
  }
}
```

The request contains:

```text
surfaceIR
layoutGrammar
surfaceKind
profile
options
```

## Required output

Rendered output must preserve the MCEL ridges needed by the extractor:

```text
data-mcel-surface-id
data-mcel-node-id
data-mcel-edge-id
data-mcel-region
data-mcel-control
data-layout-anchor-x
data-layout-anchor-y
data-layout-width
data-layout-height
data-layout-route-kind
data-layout-from-port
data-layout-to-port
```

The output must also identify:

```text
data-mcel-renderer
data-mcel-projection
```

## Verification

`McelSurfaceRendererInterface.renderWithRenderer()` can run the renderer and then call the round-trip verifier. The rendered surface is valid only when the extracted surface preserves both:

```text
semantic graph
layout grammar
```

This keeps HTML, SVG, and later renderers as projections of the same MCEL surface, not independent application definitions.
