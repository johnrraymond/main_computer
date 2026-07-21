# MCEL Shared Layout Grammar

Contract:

```text
mcel.shared-layout-grammar.v1
```

The shared layout grammar is the fourth safe MCEL foundation patch.

It sits beside `SemanticSurfaceIR` and gives rendered semantic surfaces a testable geometry contract.

```text
SemanticSurfaceIR  = what the surface means
SharedLayoutGrammar = where that meaning is intended to live and how semantic edges are routed
```

This file is domain-neutral. It does not define domain vocabulary, renderer styling, or application-specific colors.

## Core records

A shared layout grammar contains:

```text
viewport
regions
nodes
edges
controls
nodePorts
policy
```

### Viewport

The viewport defines the measurable surface area.

```text
width
height
safeMargin
```

### Regions

Regions may carry bounds.

```text
id
role
x
y
width
height
```

The bounds are optional at the IR boundary, but when they are declared they must be complete, positive, and inside the viewport.

### Nodes

Layout nodes are center-anchored boxes.

```text
id
anchorX
anchorY
width
height
z
region
homeRegion
actualRegion
teleported
```

Every semantic node should have a layout node record. A node may appear in a different actual region only when it is marked as teleported/stressed.

### Edges

Layout edges route between semantic ports.

```text
id
from
to
routeKind
fromPort
toPort
z
```

Supported route kinds are:

```text
cubic
orthogonal
polyline
straight
```

The default ports are:

```text
north
south
east
west
```

Center-to-center routing is forbidden by default. A surface may opt into it explicitly, but the default MCEL pathway should use named semantic ports.

### Controls

Layout controls are also center-anchored boxes.

```text
id
anchorX
anchorY
width
height
z
```

Every semantic control should have layout bounds.

## Validation rules

The shared layout validator checks:

```text
one positive viewport
complete and positive region bounds when declared
every semantic node has layout
every layout node references a semantic node
every semantic edge has route layout
every edge route declares route kind, from-port, and to-port
edge ports exist on the source/target nodes
every semantic control has layout
all visible boxes stay inside the viewport safe area
nodes stay inside declared region bounds when region bounds are known
visible sibling nodes do not collide
visible sibling controls do not collide
actual region differs from home region only when teleported/stressed
```

## Why this patch exists

The prior editor bug showed that an element can exist, be visible, and have nonzero dimensions while the MCEL pathway still misclassifies it as unusable.

The fix was not only "make Monaco pass." The stronger system-level lesson is:

```text
MCEL needs surface ownership plus layout contracts.
```

The shared layout grammar is the general, reusable part of that lesson.

## Boundary

This contract does not:

```text
does not define renderer appearance
does not define editor UI panels
does not define HTML or SVG output
does not define domain vocabulary
does not define the full round-trip extractor layer
```

Those come later.

## Next step

The next safe patch should add extraction helpers and round-trip verification helpers that compare:

```text
canonical SemanticSurfaceIR + SharedLayoutGrammar
against
MCEL ridges extracted from rendered HTML/SVG surfaces
```
