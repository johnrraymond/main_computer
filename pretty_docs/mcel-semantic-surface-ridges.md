# MCEL Semantic Surface Ridges

This patch adds the first small MCEL surface contract: **semantic ridges**.

A semantic ridge is stable metadata on rendered output. The visible element may be HTML, SVG, or another future projection, but rendered output must carry enough stable metadata for MCEL tooling to recover the semantic object that produced it.

This is intentionally narrow. It does not define layout grammar. It does not define a renderer. It does not define a domain vocabulary. It only defines the minimal ridge names and validation rules that later MCEL surface work can build on.

## Contract

```text
mcel.semantic-surface-ridges.v1
```

## Core ridge groups

```text
surface
node
edge
region
control
layout
```

## Required examples

A node needs stable identity, type, source, and provenance:

```text
data-mcel-node-id
data-mcel-node-type
data-mcel-source
data-mcel-provenance
```

An edge needs stable identity, kind, endpoints, and relation:

```text
data-mcel-edge-id
data-mcel-edge-kind
data-mcel-from
data-mcel-to
data-mcel-relation
```

A region and a control need stable IDs:

```text
data-mcel-region
data-mcel-control
```

## Why this belongs first

The editor and renderers can only verify a rendered surface if the rendered surface exposes stable MCEL data. This contract is the foundation for later patches:

```text
SemanticSurface IR
shared layout grammar
HTML/SVG renderers
HTML/SVG extractors
round-trip verification
editor diagnostics
```

## Boundary

This patch is MCEL-system work only. It is not an application-specific patch.
