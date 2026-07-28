# Game Runtime Patch O: Room Geometry Extraction

Patch O moves the structural mother-ship room geometry out of the monolithic
`appendShuttleBayScene()` room shell section and into room metadata.

## Goal

Mother-ship rooms now carry `rooms[].geometry` data for the visible frame of the
space:

- shell floor and ceiling bounds
- walls
- traversal openings
- door status panels
- simple structural boxes
- structural or lighting beams

The renderer interprets that data through `appendMotherShipRoomGeometry(builder,
nowMs)` before it draws bespoke gameplay props, terminal consoles, viewscreens,
and interaction hotspots.

## Runtime changes

Patch O adds:

```text
shuttle3dNormalizeMotherShipRoomGeometry(...)
appendMotherShipRoomGeometry(builder, nowMs)
```

`appendShuttleBayScene()` still owns the legacy set dressing and gameplay-specific
visuals, but the repeated room shell, wall, bridge throat, door panel, and core
corridor-throat geometry now come from room metadata.

## Data contract

Each room may include:

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

Doors remain informational. A `doorPanels[]` entry renders door status affordance
geometry, but it does not create a lock or collider.

## Validation

Patch O extends the mother-ship definition validator so room geometry is checked
for obvious content drift:

- `geometry.room`, when present, must match the containing room id
- wall axes must be `x` or `z`
- opening `exit` references must resolve to known exits
- opening and door-panel `door` references must resolve to known doors

## Non-goals

Patch O does not split the renderer into modules. That remains a later patch.
Patch O also does not convert the bridge viewscreen display into data; that is
reserved for the content-defined display patch.

## Regression intent

The existing shuttle-to-mother-ship path should remain unchanged:

```text
dock shuttle
walk through open mother-ship routes
reach bridge
use bridge viewscreen
fire bridge tactical console
disable enemy ship
```

## Corrective follow-up

Patch O.1 keeps this data-first geometry model and adds a docking handoff guard so
held shuttle-flight movement keys do not carry the player down the mother-ship
corridor immediately after arrival. It also enriches `corridor.trunk.geometry`
with visual-only rails and route lights.
