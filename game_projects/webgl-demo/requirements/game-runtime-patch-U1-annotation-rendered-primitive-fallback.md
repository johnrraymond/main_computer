# Game Runtime Patch U.1 — Annotation Rendered Primitive Fallback

## Purpose

Patch U added hold-P annotation for data-defined game elements. The first pass
could miss visible one-off runtime geometry, such as floating beams, bars, view
model pieces, and other rendered primitives that had not yet been promoted to a
stable authored data id.

Patch U.1 keeps the data-defined picker first, then adds a rendered-primitive
fallback so the user can still annotate visible geometry in the live WebGL scene.

## Runtime behavior

The shortcut remains unchanged:

```text
hold P + left-click a visible polygon or object
```

The picker now searches:

```text
stable data-defined targets
→ rendered primitive fallback targets
```

The fallback targets are captured when the renderer writes visible primitives:

```text
box
beam
console-wedge
ellipsoid
```

Each fallback target records a conservative bounds box, primitive kind, color,
source pass, and generated target key.

## Why this was needed

A visible yellow bar/beam in the mother-ship shuttle-bay route could not be
selected with P+click because it was rendered as runtime geometry rather than an
authored prop, room geometry member, interactable, or pilot station target.

Patch U.1 lets those visible objects be selected immediately while still
preserving the longer-term goal of promoting important content into stable
data-defined definitions.

## Behavior preserved

Normal gameplay controls are unchanged when P is not held.

The patch does not change:

```text
W/A/S/D movement
mouse-drag lookaround
click/Space/F phaser firing
E-key interactions
shuttle docking
mother-ship traversal
viewscreen/tactical console behavior
door traversal rules
```

## Acceptance checks

- P+click still prefers stable data-defined targets.
- P+click can select visible runtime beams/bars that were not selectable before.
- A miss reports a clear annotation hint instead of silently doing nothing.
- Saved annotations still use `metadata.shuttle3d.polygonAnnotations`.
- No locked-door progression is introduced.
