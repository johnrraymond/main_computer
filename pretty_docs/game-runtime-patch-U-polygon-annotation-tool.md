# Game Runtime Patch U — Polygon Annotation Tool

## Purpose

Patch U resumes editor-facing gameplay tooling by adding a lightweight way to select and annotate visible WebGL game elements from the live shuttle-3D surface.

The shortcut is:

```text
hold P + click a visible polygon or object
```

That opens an annotation dialog for the picked element.

## Runtime behavior

The shuttle-3D lookaround binding now treats `KeyP` as a held inspection modifier. While P is held:

- normal click-drag lookaround does not start;
- click firing is suppressed;
- the renderer casts a screen ray into the current 3D scene;
- the nearest selectable data-defined target is chosen;
- a dialog opens with label, notes, and tags fields.

The picked annotation target records:

```text
targetKey
targetId
targetKind
source
room
hit
camera
label
note
tags
updatedAt
```

Saved annotations use:

```text
schema: game.shuttle3d.polygonAnnotation.v1
```

They are stored on the scene at:

```text
metadata.shuttle3d.polygonAnnotations
```

## Selectable target sources

The first selection pass covers data-defined and runtime-declared objects that already have stable ids:

```text
rooms[].geometry.shell
rooms[].geometry.walls
rooms[].geometry.openings
rooms[].geometry.doorPanels
rooms[].geometry.boxes
rooms[].geometry.beams
motherShipInterior.props
motherShipInterior.interactables
shuttle3d.pilotStations
```

The picker intentionally resolves authored elements before anonymous runtime geometry. This keeps annotations attached to stable game-definition ids when possible.

Patch U.1 adds a fallback pass for rendered primitives that are visible but not yet authored as stable content:

```text
scene-viewer.static rendered boxes/beams/wedges/ellipsoids
scene-viewer.dynamic rendered boxes/beams/wedges/ellipsoids
```

This allows temporary or one-off visible bars, beams, and view-model pieces to be annotated instead of requiring every object to be promoted into data before it can be selected.

## Game Editor integration

The WebGL surface dispatches:

```text
main-computer-shuttle3d-polygon-annotation-save
```

The Game Editor listens for that event, writes the annotation into the active project scene metadata, and marks the project dirty. The user still saves to disk through the existing Game Editor save button and project write route.

## Behavior preserved

Patch U should not change normal gameplay:

```text
W/A/S/D movement
mouse-drag lookaround
click/Space/F phaser firing when P is not held
E-key ship interactions
shuttle flight and docking
mother-ship first-person traversal
viewscreen tracking
tactical-console firing
no locked-door progression
```

## Acceptance checks

- Holding P toggles annotation mode.
- P + left click opens an annotation dialog for the nearest selectable element.
- Saving writes `metadata.shuttle3d.polygonAnnotations`.
- The Game Editor marks the project dirty after annotation save.
- The picker targets data-defined room geometry, props, interactables, and pilot stations.
- Existing gameplay controls remain unchanged when P is not held.
- `new_patch.py --dry-run` verifies exact replacement paths.

## Follow-up

Later patches can add visible annotation pins, an annotation browser, export/import tooling, or direct project-file save actions. This patch only adds the first safe pick-and-annotate loop.


## Patch U.2 corrective note

The annotation dialog now suspends shuttle-3D gameplay keyboard handling while it
is open. Movement, firing, pilot, and inspection keys are cleared/ignored by the
game surface so the label, notes, and tags fields can receive normal typing,
including `W`, `A`, `S`, and `D`.
