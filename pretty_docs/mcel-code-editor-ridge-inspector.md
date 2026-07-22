# MCEL Code Editor Ridge Inspector

`mcel-code-editor-ridge-inspector.js` is the small visible inspector for the Code Editor MCEL surface pathway.

It builds on the surface status chip from Patch 10. The chip remains compact by default. Click, Enter, or Space opens a bounded popover that shows the current MCEL surface-pathway checks:

```text
Semantic ridges
SemanticSurfaceIR
Shared layout grammar
Surface extraction
Round-trip verification
```

The inspector is deliberately not a dock. It is lazy-opened, bounded to the title-bar action region, and hidden by default so it does not compete with the Monaco authoring surface.

## MCEL role

The inspector has its own semantic ridges:

```text
data-mcel-surface-id="code-editor.surface.mcel-ridge-inspector"
data-mcel-surface-role="supporting-diagnostic-surface"
data-mcel-node-id="code-editor.node.mcel-ridge-inspector"
data-mcel-node-type="ridge_inspector"
```

The trigger is the existing `MCEL Surface` status chip. Patch 11 adds keyboard and ARIA wiring:

```text
aria-haspopup="dialog"
aria-expanded="false"
aria-controls="code-editor-mcel-ridge-inspector"
data-mcel-ridge-inspector-trigger="code-editor"
```

## Safety rules

The inspector must remain:

```text
hidden by default
non-modal
bounded
keyboard-toggleable
outside the primary Monaco ownership path
```

That keeps detailed MCEL diagnostics visible when requested without reintroducing the earlier proof-dock/layout interference.
