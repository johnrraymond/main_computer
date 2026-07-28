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
  "props": [],
  "terminals": [],
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
  "bounds": { "minX": -4.8, "maxX": 4.8, "minZ": -39.65, "maxZ": -31.0 },
  "kind": "bridge"
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
  "position": [2.6, -5.8],
  "yaw": -0.72
}
```

Validation must confirm the spawn position is inside the referenced room bounds.

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

An interaction definition names what the terminal or object asks the runtime to do. The definition declares requirements and observable effects; the registry supplies the safe implementation.

```json
{
  "id": "fireEnemyShipVolley",
  "kind": "terminal-action",
  "requires": ["enemyShip.tracked"],
  "effects": ["enemyShip.damage", "viewscreen.weaponFire", "objective.advance"]
}
```

Version 1 should avoid arbitrary scripts. Interaction ids should map to tested runtime handlers.

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

## Validation profile

A definition should declare which validators are expected to pass.

```json
{
  "validation": {
    "requireConnectedRooms": true,
    "requireReachableTerminals": true,
    "requireInteractionHandlers": true,
    "requireObjectiveTargets": true,
    "requireSpawnInsideRoom": true
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
all terminals reference registered interactions
all objective targets exist
all spawns are inside room bounds
the room graph connects start.room to bridge.deck
the bridge viewscreen and tactical console are reachable
```

## Versioning rule

Schema v1 should be append-only where possible. New optional fields may be added, but existing field meanings should not change without a new schema id.
