# Warp Navigation Definition

Contract: `game.warp-navigation-definition.v1`

This document defines the campaign-scale navigation contract for system-to-system warp travel. It is Patch 1 of the warp line: authored data, machine-readable shape, and a forty-system reference map only.

It does not add a navigation console, route plotting, warp animation, travel execution, arrival execution, or runtime migration.

## Authority boundary

Warp navigation is project-level campaign data. The canonical authored location is:

```text
project.metadata.spaceNavigation
```

It is deliberately outside `scene.metadata.shuttle3d` and outside `motherShipInterior`:

```text
project
└── metadata.spaceNavigation       campaign-scale system graph

scene
└── metadata.shuttle3d             local-space rendering and movement
    └── motherShipInterior         walkable ship-interior graph
```

The machine-readable schema is:

```text
game_projects/schema/space-navigation.v1.schema.json
```

## Core invariant

```text
The ship is in exactly one system while not in transit.
A direct warp jump is legal only through a declared route whose origin is the current system.
Map coordinates never create connectivity.
```

Systems are graph nodes. Routes are first-class graph edges. A system does not carry a duplicated neighbor list.

## Top-level definition

```json
{
  "enabled": true,
  "schema": "game.spaceNavigation.v1",
  "definitionVersion": "game.spaceNavigation.definition.v1",
  "stateVersion": "game.spaceNavigation.state.v1",
  "startSystem": "system.solace-reach",
  "policy": {},
  "arrivalProfiles": {},
  "systems": [],
  "routes": [],
  "stateDefaults": {},
  "validation": {}
}
```

The three default projects carry the same definition:

```text
game_projects/webgl-demo/project.json
game_projects/starter-game/project.json
game_projects/new-game/project.json
```

## Policy

The first definition fixes four decisions:

```json
{
  "jumpMode": "adjacent-only",
  "directJumpRequiresRoute": true,
  "mapPositionAuthoritative": false,
  "allowDirectedRoutes": true
}
```

`adjacent-only` means the runtime may later calculate a multi-jump course, but each committed jump still traverses exactly one route.

`mapPositionAuthoritative: false` prevents the rendered chart from silently defining travel. Two systems can appear close together and still lack a route.

`allowDirectedRoutes` reserves one-way routes without requiring them in the initial map. All fifty initial routes are bidirectional.

## Systems

Each system declares identity and presentation data, not its neighbors:

```json
{
  "id": "system.solace-reach",
  "label": "Solace Reach",
  "region": "region.origin",
  "mapPosition": [0, -3],
  "localSpaceId": "space.solace-reach",
  "arrivalProfile": "arrival.bridge-retain-player"
}
```

Fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable system identity used by state and routes |
| `label` | Player-facing chart label |
| `region` | Authoring and map-grouping identity |
| `mapPosition` | Non-authoritative two-dimensional chart position |
| `localSpaceId` | Stable identity for the system-local simulation state |
| `arrivalProfile` | Named arrival behavior selected after a committed jump |

## Arrival profile

Patch 1 declares one arrival profile:

```json
{
  "arrival.bridge-retain-player": {
    "mode": "retain-player",
    "sceneId": "default-empty-scene",
    "location": "bridge.deck",
    "description": "Keep the player on the mother-ship bridge while the active local system changes."
  }
}
```

This encodes the intended first warp behavior: the mother ship changes systems while the player remains on the bridge. It does not execute that behavior yet.

## Routes

A route is the sole authority for a direct jump:

```json
{
  "id": "route.vela-gate-meridian-prime",
  "from": "system.vela-gate",
  "to": "system.meridian-prime",
  "bidirectional": true,
  "presentationDurationMs": 6500,
  "worldTimeCost": 8
}
```

`presentationDurationMs` controls future presentation time. `worldTimeCost` advances the strategic clock. They are intentionally independent.

The initial map uses:

```text
intra-region route: 4500 ms presentation, 4 world-time units
inter-region route: 6500 ms presentation, 8 world-time units
```

These values are authored defaults, not final balance.

## Forty-system reference graph

The map contains five eight-system regional loops. Every system has at least two routes before inter-region links are counted, so the initial graph has no dead ends.

| Region | Ordered loop |
| --- | --- |
| Origin Cluster | Solace Reach → Vela Gate → Carina Watch → Orison → Cinder → Lumen → Ardent → Pax → Solace Reach |
| Meridian Cluster | Meridian Prime → Tethys → Ilyra → Daedalus → Nacre → Sable → Vesper → Kestrel → Meridian Prime |
| Helix Cluster | Helix Prime → Aster → Calyx → Remora → Talon → Eos → Morrow → Halcyon → Helix Prime |
| Crown Cluster | Crown Prime → Regulus → Chiron → Bellatrix → Kepler → Lyra → Antares → Seraph → Crown Prime |
| Verge Cluster | Verge Prime → Rook → Fenris → Nyx → Osprey → Tempest → Bastion → Axiom → Verge Prime |

The ten inter-region routes are:

```text
Vela Gate ↔ Meridian Prime
Pax ↔ Kestrel
Tethys ↔ Helix Prime
Ilyra ↔ Halcyon
Sable ↔ Crown Prime
Vesper ↔ Seraph
Aster ↔ Verge Prime
Eos ↔ Rook
Regulus ↔ Axiom
Antares ↔ Bastion
```

Totals:

```text
systems: 40
routes: 50
minimum system degree: 2
start system: system.solace-reach
all systems reachable from start: yes
```

The five loops and ten bridges create alternate paths instead of a padded linear campaign chain.

## State defaults

Patch 1 declares the future state boundary:

```json
{
  "currentSystemId": "system.solace-reach",
  "plottedRouteId": null,
  "travelPhase": "in-system",
  "elapsedWorldTime": 0,
  "discoveredSystems": ["all forty authored system ids"]
}
```

All forty systems begin charted because discovery gameplay is not part of this patch line yet.

The legal travel phases reserved by the schema are:

```text
in-system
course-plotted
warp-charging
in-warp
arriving
```

Patch 1 originally reserved these phases. The browser runtime now executes them as documented in `pretty_docs/game-warp-navigation-runtime.md`.

## Validation contract

The authored definition requires future validators to prove:

```text
system ids are unique
route ids are unique
route endpoints resolve to systems
routes do not connect a system to itself
every system has at least one route
every system is reachable from the start system
arrival-profile references resolve
state references resolve
map positions are unique
```

The repository contract test proves that the current forty-system fixture satisfies these rules. The browser runtime now performs its own definition validation before accepting the graph.

## Patch boundary

Patch 1 changes only:

```text
machine-readable schema
authored project definitions
human-readable contract documentation
repository-level definition checks
```

The runtime-facing validator, deterministic jump transaction, bridge navigation console, warp presentation, and executable browser-contract proof are now implemented. Discovery, multi-jump pathfinding, encounters, save-game persistence, and differentiated destination scenes remain future work.

No runtime should infer a route from map distance, duplicate neighbor lists into systems, or replace the current system merely because an animation timer expired.
