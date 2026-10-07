# Warp Navigation Definition

Contract: `game.warp-navigation-definition.v1`

This document defines the campaign-scale navigation contract for system-to-system warp travel. The current authored fixture contains thirty-two flat star systems, forty-two routes, and richer local astronomical presentation for Solace Reach and Vela Gate.

## Authority boundary

Warp navigation is project-level campaign data:

```text
project.metadata.spaceNavigation
```

It is deliberately separate from local rendering and ship-interior state:

```text
project
└── metadata.spaceNavigation       campaign-scale graph and arrival summaries

scene
└── metadata.shuttle3d             local rendering and movement
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
Planets and companion stars never become hidden warp nodes.
```

Systems are graph nodes. Routes are first-class graph edges. A system does not carry a duplicated neighbor list.

## Top-level definition

```json
{
  "enabled": true,
  "schema": "game.spaceNavigation.v1",
  "definitionVersion": "game.spaceNavigation.definition.v2",
  "stateVersion": "game.spaceNavigation.state.v2",
  "startSystem": "system.solace-reach",
  "captainScrawl": "Congratulations, Captain, on picking the better of the two systems.",
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

```json
{
  "jumpMode": "adjacent-only",
  "directJumpRequiresRoute": true,
  "mapPositionAuthoritative": false,
  "allowDirectedRoutes": true
}
```

`adjacent-only` means every committed jump traverses exactly one declared route. `mapPositionAuthoritative: false` prevents the chart layout from silently defining travel.

All current routes are bidirectional.

## Systems

Every system is one flat warp destination:

```json
{
  "id": "system.solace-reach",
  "label": "Solace Reach",
  "region": "region.origin",
  "mapPosition": [0, -3],
  "localSpaceId": "space.solace-reach",
  "arrivalProfile": "arrival.bridge-retain-player",
  "stars": [],
  "primaryPlanet": {},
  "additionalPlanets": [],
  "arrivalDestinationId": "destination.solace-reach.haven-orbit",
  "localDestinations": [],
  "localRoutes": []
}
```

| Field | Meaning |
| --- | --- |
| `id` | Stable system identity used by state and routes |
| `label` | Player-facing chart label |
| `region` | Authoring and chart-grouping identity |
| `mapPosition` | Non-authoritative chart position |
| `localSpaceId` | Stable identity for future local simulation state |
| `arrivalProfile` | Arrival behavior after a committed jump |
| `stars` | Optional explicit stellar inventory; omission means the legacy single-star presentation |
| `primaryPlanet` | Default arrival and bridge-viewscreen planet |
| `additionalPlanets` | Additional charted worlds inside the same warp destination |
| `arrivalDestinationId` | Local destination selected when an inter-system arrival commits |
| `localDestinations` | Authored places reachable inside this system; never warp-chart nodes |
| `localRoutes` | Authored movement edges between local destinations |

A planet may also declare:

| Field | Meaning |
| --- | --- |
| `habitable` | The world supports ordinary habitation under the authored setting |
| `inhabited` | A population or permanent habitat is present |
| `formerSystemId` | Provenance for a planet retained from a removed warp destination |

## Flat chart and local-system boundary

The destination selector presents thirty-two peer systems. It never renders `stars`, `primaryPlanet`, `additionalPlanets`, or `formerSystemId` as separate warp choices.

```text
warp destination
→ one star system
→ stars, planets, moons, stations, habitats, fleets, and hazards
```

The previous subsystem proposal is rejected. There is no `subsystems` field in the schema or runtime.

## Rich systems

Solace Reach and Vela Gate each author:

```text
2 stars
1 primary planet
4 additional planets
5 inhabited worlds total
at least 2 explicitly habitable worlds
```

Content preservation:

```text
Solace Reach: Haven, Osprey, Bellara, Lyria, Talon
Vela Gate: Velaris, Antares, Seraph, Bastion, Chiron
```

The eight former system ids are absent from the system list, routes, and discovery state. Their planet ids remain stable inside `additionalPlanets`.

## Local navigation contract

Definition version `game.spaceNavigation.definition.v2` adds an optional all-or-nothing local navigation contract to a system:

```json
{
  "arrivalDestinationId": "destination.solace-reach.haven-orbit",
  "localDestinations": [
    {
      "id": "destination.solace-reach.haven-orbit",
      "label": "Haven High Orbit",
      "kind": "planet-orbit",
      "parentBodyId": "planet.haven",
      "position": [0, 0],
      "discoveredByDefault": true,
      "availableByDefault": true,
      "visualProgram": "systemPlanet",
      "description": "The default arrival orbit above Haven."
    }
  ],
  "localRoutes": [
    {
      "id": "local-route.solace-reach.haven-osprey",
      "from": "destination.solace-reach.haven-orbit",
      "to": "destination.solace-reach.osprey-anchorage",
      "bidirectional": true,
      "presentationDurationMs": 1800,
      "worldTimeCost": 1
    }
  ]
}
```

A system that declares any of these three fields must declare all three. Destination ids and route ids are globally unique. Destination parent bodies must resolve inside the same system, route endpoints must resolve inside the same local graph, and every authored local destination must be reachable from the arrival destination.

Solace Reach and Vela Gate each now declare five planet-orbit destinations and five connected local routes. The other thirty systems remain compatible through a deterministic primary-orbit fallback until richer local definitions are authored.

This patch establishes identities, references, topology, arrival selection, and save-state restoration. It does not yet expose local course plotting or transit controls.

## Arrival profile

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

The mother ship changes systems while the player remains on the bridge.

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

`presentationDurationMs` controls presentation time. `worldTimeCost` advances the strategic clock. They remain independent.

## Thirty-two-system reference graph

The graph retains all thirty-two surviving systems and contracts corridors that previously passed through removed nodes.

Regional system counts:

| Region | Warp systems |
| --- | ---: |
| Origin | 8 |
| Meridian | 8 |
| Helix | 7 |
| Crown | 3 |
| Verge | 6 |

Contracted replacement corridors:

```text
Remora ↔ Eos
Regulus ↔ Kepler
Kepler ↔ Crown Prime
Nyx ↔ Tempest
Tempest ↔ Axiom
Vesper ↔ Crown Prime
Kepler ↔ Tempest
```

Totals:

```text
systems: 32
routes: 42
preserved planet identities: 40
explicit binary systems: 2
five-world systems: 2
minimum system degree: 2
start system: system.solace-reach
all systems reachable from start: yes
```

## State defaults

```json
{
  "currentSystemId": "system.solace-reach",
  "currentLocalDestinationId": "destination.solace-reach.haven-orbit",
  "plottedRouteId": null,
  "travelPhase": "in-system",
  "elapsedWorldTime": 0,
  "discoveredSystems": ["all thirty-two warp-system ids"],
  "discoveredLocalDestinations": {
    "system.solace-reach": ["five authored destination ids"],
    "system.vela-gate": ["five authored destination ids"]
  }
}
```

The legal phases are:

```text
in-system
course-plotted
warp-charging
in-warp
arriving
```

All thirty-two systems begin charted. The eight removed ids do not survive in discovery state.

## Captain's Scrawl

The definition carries one route-choice affirmation:

> Congratulations, Captain, on picking the better of the two systems.

The basic graph does not rank destinations. The browser presentation shows the scrawl after a course is plotted and during travel. Campaign scenario logic remains responsible for making the selected major destination a just-in-time intervention.

## Validation contract

Validators prove:

```text
system, route, planet, and explicit star ids are unique
route endpoints resolve to the thirty-two systems
routes do not connect a system to itself
every system has at least two reachable neighbors in the fixture
every system is reachable from Solace Reach
arrival-profile and state references resolve
map positions are valid and unique
every system has a primary planet
additional planets are complete planet records
local destination and route ids are unique
local destination parent bodies and route endpoints resolve within their system
each authored local graph is reachable from its arrival destination
current and discovered local-destination state references resolve
removed system ids are absent from topology and state
```

The browser runtime validates the definition before accepting it.

## Implementation boundary

Implemented:

- flat thirty-two-system topology;
- forty-two routes;
- richer stellar and planetary records;
- runtime arrays and counts for current and destination bodies;
- ten authored local destinations and ten local routes across the first two systems;
- local arrival identity, discovery state, save restoration, and warp-arrival commit;
- deterministic primary-orbit fallback for systems without an authored local graph;
- Captain's Scrawl transport into the browser UI.

Not implemented:

- local course plotting and travel between destinations;
- player-facing local-destination selection controls;
- multiple-body viewscreen rendering;
- local scenario persistence;
- multi-jump pathfinding;
- discovery gameplay.

Navigation remains responsible for legal campaign travel. Local-space and scenario runtimes must own movement and consequences after arrival.
