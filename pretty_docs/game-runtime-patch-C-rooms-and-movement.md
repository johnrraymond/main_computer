# Patch C: Mother-Ship Rooms and Movement Extraction

Contract: `game.runtime-patch-C-rooms-and-movement.v1`

Patch C moves the current mother-ship room map and first-person movement envelope into explicit level-definition data while preserving player-facing behavior.

## Scope

Patch C keeps the existing playable route intact:

```text
shuttle-bay arrival
→ Starboard Interior Access
→ Bay Operations
→ Security Checkpoint
→ Main Corridor Hub
→ Engineering / Medbay / Science branches
→ Bridge Access
→ Bridge Deck
→ Bridge viewscreen and tactical console
```

The patch does not add new rooms, new locked-door behavior, new combat, or new bridge outcomes.

## New runtime boundary

The renderer now has a level-default factory:

```text
shuttle3dMotherShipInteriorLevelDefaults()
```

That factory is the source for:

```text
mother-ship movement bounds
walkable room rectangles
room-to-state-location mapping
room priority for overlapping thresholds
open traversal exits
shuttle-bay arrival spawn and facing
static ship colliders
```

`shuttle3dMotherShipInteriorConfig(scene)` now normalizes `rooms`, `exits`, `spawns`, and `movement` from project metadata with runtime fallbacks for older projects.

## Why this matters

Earlier mother-ship defects came from different systems disagreeing:

```text
the bridge was modeled but outside movement bounds
a black doorway looked open but did not connect to a modeled corridor
location sync used hardcoded z/x checks that could drift from room geometry
spawn/facing lived separately from the rest of the ship map
```

Patch C makes room and movement data explicit so later validators can prove that the player can reach each room, terminal, objective, and bridge feature.

## Room priority

Some rectangular regions overlap at thresholds. Each room therefore has a numeric priority. Higher priority wins during location detection.

Examples:

```text
bay.shuttle beats bay.ops at the shuttle-bay threshold
bridge.deck beats bridge.access at the bridge threshold
engineering/medbay/science beats corridor.main in side-room overlaps
corridor.trunk maps back to corridor.main for state/HUD purposes
```

## Acceptance checks

Patch C is acceptable when:

```text
existing gameplay remains reachable
rooms are declared in motherShipInterior.rooms
movement bounds are declared in motherShipInterior.movement
the shuttle-bay spawn is declared in motherShipInterior.spawns
bridge.deck remains inside global movement bounds
terminal.bridge-viewscreen remains reachable
shipLocationForPosition uses room data instead of a chain of hardcoded coordinate checks
```

## Next patch

Patch D should extract terminals/interactables:

```text
terminal ids
labels
prompt text
positions
interaction radius
objective targets
```

That patch should still preserve behavior while preparing for Patch E's named interaction registry.
