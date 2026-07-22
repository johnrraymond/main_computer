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
