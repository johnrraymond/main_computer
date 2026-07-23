# MCEL Surface Preview Contract

`mcel.surface-preview-contract.v1` defines the host-neutral preview boundary for MCEL authored semantic surfaces.

It does not add editor UI and it does not define a domain-specific renderer. It connects existing MCEL pieces:

```text
authored source text
  -> McelAuthoredSurfaceDocument
  -> SemanticSurfaceIR
  -> SharedLayoutGrammar
  -> McelSurfaceRendererInterface
  -> rendered preview projection
  -> round-trip verification
```

## Main APIs

```javascript
McelSurfacePreviewContract.renderPreview({
  sourceText,
  renderer,
  surfaceKind: "html" // or "svg"
});

McelSurfacePreviewContract.renderPreview({
  surfaceIR,
  layoutGrammar,
  renderer,
  surfaceKind: "svg"
});

McelSurfacePreviewContract.renderPreviewPair({
  surfaceIR,
  layoutGrammar,
  htmlRenderer,
  svgRenderer
});
```

## Contract rules

- Plain text with no MCEL ridges is `not-applicable`, not a failure.
- Preview rendering requires a `SemanticSurfaceIR` and `SharedLayoutGrammar`.
- Renderers are explicit inputs; the preview contract does not invent renderer behavior.
- Renderer output is checked through the renderer interface and round-trip verifier.
- HTML/SVG preview pairs can be checked for extracted semantic/layout agreement.
- The module is domain-neutral and does not depend on a particular application.

## Intended hosts

Any host can call the preview contract:

```text
Code Editor
MCEL Lab
Website Builder
test runner
debug tools
future visual editor
```

The Code Editor may consume this later, but the preview contract itself belongs to MCEL core.
