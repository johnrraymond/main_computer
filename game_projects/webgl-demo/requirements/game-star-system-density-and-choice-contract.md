# Star-System Density and Just-in-Time Choice Contract

Contract: `game.star-system-density-choice.v1`

Status: working design authority with authored local-navigation contracts for Solace Reach and Vela Gate.

## Design objective

The campaign uses a flat warp chart with thirty-two selectable star systems. Selected systems are astronomically and politically denser so important action can overlap inside one local situation instead of requiring a warp jump after every major event.

At a major paired destination choice, whichever route the player selects must feel like the correct path through the campaign. The captain arrives just in time to affect something large and consequential.

## Model correction

The game does not present complete star systems as children of another star system:

```text
wrong warp UI
Solace Reach
├── Osprey
├── Bellatrix
├── Lyra
└── Talon
```

The implemented campaign-scale model is:

```text
flat warp chart
├── Solace Reach
├── Vela Gate
├── Pax
└── twenty-nine other peer systems

Solace Reach local astronomy
├── Solace A
├── Solace B
├── Haven
├── Osprey
├── Bellara
├── Lyria
└── Talon

Vela Gate local astronomy
├── Vela A
├── Vela B
├── Velaris
├── Antares
├── Seraph
├── Bastion
└── Chiron
```

The eight former system identities are no longer warp nodes. Their planet identities and authored scenario material remain available as local-world content inside the first two systems.

## Location terminology

| Term | Meaning |
| --- | --- |
| Warp destination | A named star system selectable through the bridge navigation interface |
| Star system | The complete local astronomical and political space associated with one warp destination |
| Astronomical body | A star, planet, moon, dwarf planet, or similar natural object inside a star system |
| Local destination | A body, settlement, station, fleet, wreck, beacon, habitat, or hazard reachable after arrival |
| Absorbed local world | A former warp destination whose planet and scenario content now belong inside a richer system |
| Major destination choice | A campaign-framed choice between systems that can each carry the next central chapter |
| Routine travel | A jump, return trip, or logistical move not framed as a major campaign branch |

Do not use `subsystem` for the absorbed worlds. It incorrectly suggests another warp-selectable star system or a nested navigation hierarchy.

## Flat navigation contract

- Every selectable item represents one complete star system.
- The destination selector contains no parent/child grouping or indentation.
- Planets, companion stars, stations, and other local destinations never appear as extra warp nodes.
- Routes connect the thirty-two star systems only.
- Internal bodies may appear in system detail and local-space interfaces without altering warp topology.
- The removed ids never appear in routes, discovery state, or the destination list.

The removed warp ids are:

```text
system.osprey
system.bellatrix
system.lyra
system.talon
system.antares
system.seraph
system.bastion
system.chiron
```

Their former primary planets remain authored with stable planet ids and `formerSystemId` provenance.

## Implemented rich-system composition

### Solace Reach

Solace Reach is a binary system with two explicitly authored stars and five charted inhabited worlds.

| Body | Role |
| --- | --- |
| Solace A | G-class primary star |
| Solace B | K-class companion star |
| Haven | Primary refuge world |
| Osprey | Habitable island and shallow-sea world |
| Bellara | Habitable ocean world |
| Lyria | Inhabited ice world |
| Talon | Inhabited rocky industrial world |

### Vela Gate

Vela Gate is a binary system with two explicitly authored stars and five charted inhabited worlds.

| Body | Role |
| --- | --- |
| Vela A | F-class primary star |
| Vela B | M-class companion star |
| Velaris | Primary ocean world |
| Antares | Inhabited volcanic world |
| Seraph | Inhabited cloud world and habitat zone |
| Bastion | Habitable high-gravity mountain world |
| Chiron | Habitable rocky world |

The navigation runtime now gives each rich system an authored local arrival destination, five planet-orbit destinations, and five connected local routes. The current local destination selects the parent planet projected through the legacy viewscreen fields, while player-facing local transit remains deferred.

## Content preservation map

| Former warp system | New local owner | Preserved planet |
| --- | --- | --- |
| Osprey | Solace Reach | Osprey |
| Bellatrix | Solace Reach | Bellara |
| Lyra | Solace Reach | Lyria |
| Talon | Solace Reach | Talon |
| Antares | Vela Gate | Antares |
| Seraph | Vela Gate | Seraph |
| Bastion | Vela Gate | Bastion |
| Chiron | Vela Gate | Chiron |

The regional scenario dossiers preserve these eight scenario concepts as local-world threads under the Origin Cluster document. Their previous region placement remains historical context, not current warp topology.

## Action-concentration contract

A dense system should support several interacting situations:

```text
arrive at an outer beacon
→ respond to an immediate crisis
→ travel to another inhabited world
→ discover a connected conflict near the companion star
→ choose which population or institution receives support
→ revisit an altered local destination
```

Concentration should increase visible cause and effect, faction reaction speed, simultaneous pressure, and opportunities to revisit changed places.

It must not become a checklist of unrelated missions sharing one skybox. The absorbed scenarios need connective tissue explaining shared transport, institutions, resources, communications, and consequences inside their new systems.

## Route graph after contraction

The warp graph contains:

```text
32 systems
42 bidirectional routes
40 preserved planet identities
2 explicitly binary systems
2 five-world systems
minimum system degree: 2
complete reachability from Solace Reach
```

Fifteen routes that referenced removed systems were replaced by seven contracted corridors between surviving systems. This preserves regional circulation and cross-region access without leaving hidden or invalid destinations.

## Major destination choice contract

Whenever the campaign presents two systems as competing next major paths, both must be authored as central routes.

Whichever system the player selects:

1. the route is affirmed;
2. arrival occurs during a decisive intervention window;
3. something large is already in motion;
4. the mother ship or captain has a credible reason to matter;
5. the scenario can produce persistent consequences;
6. the player can later understand why arriving at that moment mattered.

The Captain's Scrawl is:

> Congratulations, Captain, on picking the better of the two systems.

The runtime exposes this text from the navigation definition. The bridge navigation panel displays it after a course is plotted and while the ship is travelling. It does not identify one destination as objectively superior.

## Destination affirmation versus moral judgment

The affirmation means:

```text
You came to the right place at the right time.
```

It does not mean:

```text
Everything you do here is morally correct.
```

Scenario outcomes may still be praised, questioned, feared, misunderstood, or left unresolved. The route is validated as important while the player's conduct remains consequential.

## Just-in-time arrival

A selected major destination must contain a critical event satisfying four tests:

| Test | Requirement |
| --- | --- |
| Urgency | Delay would materially change the situation |
| Scale | The outcome affects populations, institutions, routes, ecosystems, or the wider campaign |
| Relevance | The captain has evidence, capabilities, authority, mobility, or independence local actors lack |
| Persistence | The system visibly remembers the intervention |

The opening pair remains Vela Gate and Pax. Either can become the next central chapter and either receives the same route affirmation.

## Unchosen destination rule

The unchosen system remains narratively viable. It may remain unresolved, evolve, be temporarily contained, or reach another intervention window later. It must not routinely collapse merely to prove that the player chose incorrectly.

## Current implementation boundary

Implemented:

- thirty-two flat warp destinations;
- forty-two routes with complete reachability;
- removal of all eight former system ids from routes and discovery state;
- two binary systems;
- five charted worlds in each of the first two systems;
- stable absorbed-planet identities and provenance;
- runtime body counts and arrays;
- five authored local destinations and five connected local routes in each rich system;
- local destination identity, discovery state, save restoration, and warp-arrival selection;
- flat destination UI with no subsystem grouping;
- route-choice Captain's Scrawl presentation.

Not yet implemented:

- local course plotting and travel among the five worlds;
- player-facing local-destination selection controls;
- multiple simultaneous system scenarios;
- local persistence for every absorbed scenario;
- star and multi-planet viewscreen rendering;
- adaptive unchosen-destination evolution;
- a campaign state record distinguishing major choices from routine travel.

## Acceptance criteria

1. The warp UI shows thirty-two peer systems and no nested nodes.
2. Removed system ids are absent from systems, routes, and discovery state.
3. Solace Reach and Vela Gate each author two stars and five worlds.
4. At least two worlds in each rich system are explicitly habitable.
5. All eight removed planet identities remain present exactly once.
6. Every route endpoint resolves to one of the thirty-two systems.
7. The graph remains connected and every system has at least two neighbors.
8. The selected destination remains an ordinary flat UI entry.
9. Plotting either major route can display the same better-choice affirmation.
10. The affirmation validates timing and importance, not every later action.
11. The unchosen destination remains narratively viable.
12. Local destination identities and route graphs may be claimed as implemented; player-controlled local transit and scenario concentration may not.
