# MCEL Semantic Surface IR

This document describes `mcel.semantic-surface-ir.v1`.

The semantic surface IR is the first typed MCEL model that sits after semantic ridges and before renderer-specific output. It is intentionally domain-neutral.

```text
rendered ridge records
  -> semantic surface ridge contract
  -> SemanticSurfaceIR
  -> later layout grammar, renderers, extractors, and editor diagnostics
```

## Boundary

This patch defines the in-memory model for a semantic application surface:

```text
surface
regions
nodes
edges
controls
optional layout records carried by ridge metadata
```

It does not define a domain vocabulary.

It does not define renderer behavior.

It does not define the full layout grammar.

It does not add application-specific assumptions.

## Why this exists

Semantic ridges prove that rendered output carries enough stable metadata to be inspected.

The SemanticSurfaceIR turns those records into a reusable canonical model so the main MCEL pathway can reason over a surface without depending on HTML, SVG, or editor DOM details.

## Core objects

```text
SemanticSurfaceIR
  surface
  graph.nodes
  graph.edges
  graph.regions
  graph.controls
  layout.nodes
  layout.edges
  layout.controls
```

The graph is the semantic model. The layout section is only the layout data already present on ridges. A later patch will promote layout into a full shared grammar with viewport and route rules.

## Safety rules in v1

The v1 validator checks:

```text
one identifiable surface
stable node, edge, region, and control ids
node type/source/provenance
edge source and target references
edge kind and relation
node region references
layout references to graph objects
positive layout width and height when present
ported edge layouts declaring a route kind
```

## Round trip

The module can build IR from ridge records, export it back to ridge records, and compare canonical fingerprints. This is the first reusable round-trip boundary for MCEL semantic surfaces.
