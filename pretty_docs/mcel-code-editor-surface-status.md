# MCEL Code Editor Surface Status

`mcel-code-editor-surface-status.js` makes the Patch 09 surface-diagnostics plumbing visible in the Code Editor title bar.

It mounts a compact status chip:

```text
MCEL Surface PASS
```

The chip reads `report.mcelSurfacePathway` from the Code Editor self-diagnosis report and summarizes these checks:

```text
semantic ridges
SemanticSurfaceIR
SharedLayoutGrammar
surface extraction
round-trip verification
```

The visual state is data-driven:

```text
data-mcel-surface-status-state="pass"
data-mcel-surface-status-state="fail"
data-mcel-surface-status-state="warning"
data-mcel-surface-status-state="unavailable"
data-mcel-surface-status-state="pending"
```

This patch intentionally does not add a full inspector. It only makes the existing pathway visible so a user can see whether the editor surface is passing the MCEL semantic/layout round-trip contract.

## Host conformance fallback

When the selected source file is an ordinary source document rather than an authored MCEL surface, the visible status chip is allowed to use the Code Editor app-surface conformance report as the authoritative host signal.  A passing `appSurfaceConformance` report with no active errors renders the chip as `MCEL Surface PASS` even when the authored-surface round-trip pathway is unavailable for the current plain source file.

This prevents the title-bar chip from reporting a false MCEL failure while the runtime `[e/w/g]` diagnostics counter is green.

## Patch 22b host-workbench status correction

The Code Editor `MCEL Surface` chip is a host/workbench status, not a demand
that every ordinary source file be an authored MCEL surface document.  For
ordinary files, the chip may use passing app-surface conformance as the source
of truth while still retaining the authored/pathway details for the ridge
inspector.

The runtime owner threshold is intentionally lower than the rich document/file
surface threshold: a visible Monaco editor around 360px wide by 320px tall is
considered usable.  Hidden zero-sized optional context panes are not included in
the Code Editor surface layout grammar.
