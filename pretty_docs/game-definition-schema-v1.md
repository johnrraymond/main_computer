# Game Definition Schema v1

Contract: `game.definition-schema.v1`

This document describes the first data shape the games should migrate toward. The companion machine-readable schema is:

```text
game_projects/schema/game-definition.v1.schema.json
```

The schema is intentionally focused on the systems that have caused defects during mother-ship expansion: rooms, movement bounds, exits, props, terminals, objectives, encounters, and validation hints.

## Top-level shape

```json
{
  "schema": "game.definition.v1",
  "id": "mother-ship-interior",
  "title": "Mother Ship Interior",
  "version": 1,
  "start": {
    "room": "bay.shuttle",
    "spawn": "spawn.shuttle-bay"
  },
  "rooms": [],
  "exits": [],
  "spawns": [],
  "movement": {},
  "props": [],
  "terminals": [],
  "interactables": [],
  "interactions": [],
  "objectives": [],
  "encounters": [],
  "stateDefaults": {},
  "validation": {}
}
```

## Coordinates

The current WebGL scene uses an X/Z floor plane. The schema keeps that convention.

```text
x: left/right across the ship
z: forward/back along the ship route
```

Every playable room must define rectangular bounds:

```json
{
  "id": "bridge.deck",
  "name": "Bridge Deck",
  "bounds": { "minX": -4.8, "maxX": 4.8, "minZ": -39.65, "maxZ": -31.0 }
}
```

Later schemas can add polygons. Version 1 should keep rectangles so validation is simple.

## Rooms

A room is both a gameplay location and a rendering/collision unit.

Required fields:

```json
{
  "id": "bridge.deck",
  "name": "Bridge Deck",
  "location": "bridge.deck",
  "bounds": { "minX": -4.8, "maxX": 4.8, "minZ": -39.65, "maxZ": -31.0 },
  "kind": "bridge",
  "priority": 110,
  "visual": {
    "color": "#ef4444",
    "edgeColor": "#f97316",
    "labelColor": "#fee2e2",
    "boundary": true,
    "floorBand": true,
    "label": true,
    "labelHeight": 0.42
  }
}
```

Recommended room ids:

```text
bay.shuttle
bay.starboard-access
bay.ops
security.checkpoint
corridor.hub
engineering.access
bridge.access
bridge.deck
```

Patch N adds room visual metadata. The renderer uses `rooms[].visual` for a data-driven room boundary pass, so visual wayfinding can follow the same room bounds used by movement and location detection. Supported visual fields are:

```text
color
edgeColor
labelColor
boundary
floorBand
label
labelHeight
```

Patch O adds room geometry metadata. The renderer uses `rooms[].geometry` for the reusable structural room pass before bespoke set dressing. Supported geometry fields are:

```text
schema
shell
walls
openings
doorPanels
boxes
beams
lighting
```
Patch O.1 uses the same `rooms[].geometry` contract for corridor-trunk recovery:
`boxes` may define non-blocking visible rail/guide-strip affordances and `beams`
may define long route lights. These entries are visual only; movement remains
controlled by room bounds, movement colliders, and the no-locked-door rule.


Example:

```json
{
  "id": "bridge.deck",
  "geometry": {
    "schema": "game.room.geometry.v1",
    "shell": {
      "bounds": { "minX": -4.8, "maxX": 4.8, "minZ": -39.5, "maxZ": -31.25 },
      "accentColor": "#0ea5e9"
    },
    "walls": [
      { "axis": "x", "x": -4.8, "minZ": -39.5, "maxZ": -31.25 },
      { "axis": "z", "z": -39.5, "minX": -4.8, "maxX": 4.8 }
    ],
    "openings": [
      { "id": "opening.bridge-deck-throat", "exit": "exit.bridge-deck" }
    ],
    "doorPanels": []
  }
}
```

Geometry openings document traversal gaps and may reference exits or doors. They do not imply locked-door progression.

## Exits

An exit connects two rooms. Doors are not progression locks. An exit may be visually represented by a hatch, frame, threshold, or status panel, but traversal should stay open unless a future design explicitly adds hazards or physical blockers.

```json
{
  "id": "exit.bridge-access-to-deck",
  "from": "bridge.access",
  "to": "bridge.deck",
  "bounds": { "minX": -2.2, "maxX": 2.2, "minZ": -31.5, "maxZ": -31.0 },
  "label": "Bridge Entry"
}
```

Validation must confirm both referenced rooms exist and the exit touches or overlaps both room bounds.

## Spawns

A spawn point names a safe position and facing direction.

```json
{
  "id": "spawn.shuttle-bay",
  "room": "bay.shuttle",
  "position": [0.24, 0.9, 4.3],
  "yaw": 32,
  "pitch": -4
}
```

Validation must confirm the spawn position is inside the referenced room bounds.

## Movement

Movement declares the global playable envelope and fixed collision boxes used by the first-person controller. It should cover every playable room; individual room bounds still define which spaces count as walkable.

```json
{
  "bounds": { "minX": -9.8, "maxX": 9.8, "minZ": -39.65, "maxZ": 5.12 },
  "colliders": [
    { "id": "docked-shuttle-hull", "minX": -1.36, "maxX": 1.36, "minZ": -1.42, "maxZ": 1.44 }
  ]
}
```

Validation must confirm each room is inside movement bounds and each collider has valid min/max coordinates.

## Props

Props are non-interactive visual objects or rendered systems.

```json
{
  "id": "prop.bridge-viewscreen",
  "room": "bridge.deck",
  "kind": "viewscreen",
  "position": [0.0, -38.2],
  "size": [7.2, 2.4],
  "display": "enemy-raider-tactical"
}
```

A prop may have renderer-specific hints, but the core definition should remain stable.

Patch H makes these props active in the mother-ship renderer. Patch I adds data-defined map markers so repeated console and route markers are authored as content instead of hardcoded renderer calls. Patch M adds `terminal-console` props so visible terminal and tactical console bodies can be authored as content data while E-key behavior still lives in interactables/interactions. Patch P adds `viewscreen` props whose `display` value selects a state-driven display program such as `enemyShipTactical`. The first supported procedural prop kinds are:

```text
floor-marker
map-marker
sign
beacon
light-strip
status-panel
terminal-console
viewscreen
```

Validation should confirm each authored prop references an existing room, sits inside that room, remains inside playable movement bounds, points any optional `target` at known content such as a room, terminal, door, interactable, objective, or allowed runtime system target, and points any optional `display` at a supported display program.

Patch J adds prop-target validation through `requirePropTargets` so map markers and status panels cannot silently point at missing content. Patch P extends the same validation path to content-defined displays.

## Terminals

Terminals are interactable props. Their behavior is named by interaction id.

```json
{
  "id": "terminal.bridge-tactical",
  "room": "bridge.deck",
  "position": [3.2, -34.8],
  "radius": 1.35,
  "label": "Tactical Console",
  "prompt": "Press E to fire tactical volley",
  "interaction": "fireEnemyShipVolley"
}
```

Validation must confirm:

```text
terminal room exists
terminal position is inside that room
terminal interaction exists in the interaction registry
terminal prompt is non-empty
terminal radius is positive
```

## Interactions

An interaction definition names what the terminal or object asks the runtime to do. The definition declares a safe handler id and optional observable status/effects; the runtime registry supplies the implementation.

```json
{
  "id": "fireBridgeTacticalConsole",
  "label": "Fire Bridge Tactical Console",
  "handler": "fireBridgeTacticalConsole",
  "requires": ["enemyShip.tracked"],
  "effects": ["enemyShip.damage", "viewscreen.weaponFire", "objective.advance"],
  "emitsState": true
}
```

Patch E also allows the current nested project metadata form to use an object map:

```json
{
  "interactions": {
    "fireBridgeTacticalConsole": {
      "id": "fireBridgeTacticalConsole",
      "label": "Fire Bridge Tactical Console",
      "handler": "fireBridgeTacticalConsole"
    }
  }
}
```

Version 1 should avoid arbitrary scripts. Interaction ids should map to tested runtime handlers, and validators should fail content where an interactable action has no interaction registry entry.

## Objectives

Objectives should point to real rooms or interactables.

```json
{
  "id": "bridge.attack-raider",
  "text": "Use the tactical console to attack the enemy ship.",
  "target": "terminal.bridge-tactical",
  "completeWhen": "enemyShip.disabled"
}
```

Validation must confirm the objective target id exists.

## Encounters

Encounters describe enemy or hazard state without embedding combat code.

```json
{
  "id": "encounter.enemy-raider",
  "kind": "ship",
  "stateKey": "enemyShip",
  "defaults": {
    "hull": 100,
    "status": "tracked"
  }
}
```

The runtime owns how `enemyShip` changes when interactions fire.

## State defaults

State defaults initialize runtime state and provide migration defaults for older saves/projects.

```json
{
  "location": "bay.shuttle",
  "objective": "bridge.track-raider",
  "enemyShip": {
    "hull": 100,
    "status": "standby"
  },
  "terminals": {
    "terminal.bridge-viewscreen": { "status": "standby" },
    "terminal.bridge-tactical": { "status": "armed" }
  }
}
```


## Interactables

An interactable is the gameplay target that actually produces a prompt and receives the E-key. Terminals can describe system state, while interactables define player reachability, prompt copy, and action ids.

```json
{
  "id": "terminal.bridge-tactical",
  "kind": "terminal",
  "room": "bridge.deck",
  "position": [2.85, -36.7],
  "radius": 1.85,
  "label": "Bridge Tactical Console",
  "prompt": "Press E to fire Bridge Tactical Console.",
  "action": "fireBridgeTacticalConsole",
  "visual": {
    "color": "#f97316",
    "activeColor": "#fef3c7",
    "radiusScale": 0.38,
    "height": 0.72,
    "activeHeight": 1.02,
    "baseSize": 0.2,
    "terminalPanel": true,
    "routeBeam": false
  }
}
```

Patch D extracts current mother-ship E-key targets into `motherShipInterior.interactables`. Patch E resolves each interactable `action` through `motherShipInterior.interactions` before invoking a safe runtime handler. Patch K renders those interactables as in-world hotspots. Patch L adds optional `visual` metadata so the authored content can style hotspot color, active color, radius scaling, height, base size, terminal-panel decoration, and access/door route beams. A later schema migration can map that nested project metadata directly into top-level `interactables` and `interactions`.

Validators should confirm that every interactable is inside a reachable room, has a non-empty prompt, points to a registered action handler, and leaves visual metadata as presentation-only data.

## Validation profile

A definition should declare which validators are expected to pass.

```json
{
  "validation": {
    "requireConnectedRooms": true,
    "requireReachableTerminals": true,
    "requireReachableInteractables": true,
    "requireInteractionHandlers": true,
    "requireObjectiveTargets": true,
    "requireSpawnInsideRoom": true,
    "requirePropTargets": true
  }
}
```

## Required validators for the first implementation

The first validator patch should check:

```text
all room ids are unique
all rooms have finite min/max bounds
all exits reference existing rooms
all terminals reference existing rooms
all terminals are inside room bounds
all terminals reference existing rooms
all interactables are inside room bounds
all interactables reference registered actions
all terminals reference registered interactions
all objective targets exist
all spawns are inside room bounds
all prop targets point to known rooms, terminals, doors, interactables, objectives, or supported runtime systems
the room graph connects start.room to bridge.deck
the bridge viewscreen and tactical console are reachable
```

## Versioning rule

Schema v1 should be append-only where possible. New optional fields may be added, but existing field meanings should not change without a new schema id.

### Patch F validation rules

Patch F makes validation executable for the mother-ship interior runtime. The same ideas should be preserved for future `game.definition.v1` data.

```json
{
  "validation": {
    "requireRoomBoundsInsideMovement": true,
    "requireConnectedRooms": true,
    "requireReachableInteractables": true,
    "requireInteractionHandlers": true,
    "requireObjectiveTargets": true,
    "requireSpawnInsideRoom": true,
    "requireOpenDoors": true
  }
}
```

The runtime validator should produce a report with this shape:

```json
{
  "schema": "game.motherShipInterior.validation.v1",
  "ok": true,
  "errors": [],
  "warnings": []
}
```

These checks are meant to catch mismatches before a player reaches them: rooms outside movement bounds, prompts without handlers, terminals outside room bounds, objectives targeting missing locations, and reintroduced locked doors.
