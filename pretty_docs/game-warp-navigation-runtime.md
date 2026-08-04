# Game Warp Navigation Runtime

## Status

Warp navigation is executable in the browser game surface.

The runtime loads `project.metadata.spaceNavigation`, validates a flat thirty-two-system graph with forty-two warp routes plus the first two local graphs, exposes directly connected destinations, and executes a deterministic five-phase jump transaction.

## Runtime boundary

The graph engine is isolated in:

```text
main_computer/web/applications/scripts/space-navigation-runtime.js
```

It owns:

```text
definition validation
current-system state
flat adjacent-destination projection
stellar and planetary summary projection
local-destination identity and discovery state
local arrival selection and save-state restoration
course plotting
warp engagement
phase progression
arrival commit
world-time advancement
Captain's Scrawl transport
```

`scene-viewer.js` owns physical bridge-console access, HUD updates, player movement, and browser presentation. It does not infer routes from chart coordinates and does not create subsystem groups.

## Player operation

```text
Reach Bridge Navigation Console
→ stand within its interaction range
→ press E
→ select an adjacent system
→ plot course
→ receive the route affirmation
→ engage warp
```

There is no global navigation shortcut or floating navigation button. Moving away from the physical console closes the session.

At Solace Reach, the legal destinations remain Pax and Vela Gate. Meridian Prime is not directly connected.

## Flat destination presentation

Every selector option represents one system. The UI contains no `<optgroup>`, parent id, child id, or subsystem indentation.

Ordinary destinations show their primary planet. Rich destinations may additionally show star and world counts:

```text
Vela Gate • 2 stars • 5 worlds • 4 time
```

The counts describe the selected system; they do not create additional choices.

## Rich-system projection

The runtime preserves the legacy primary-planet fields and also exposes:

```text
currentPlanets
currentStars
currentPlanetCount
currentStarCount
currentHabitablePlanetCount
destinationPlanets
destinationStars
destinationPlanetCount
destinationStarCount
```

`currentPlanet` and `destinationPlanet` remain the primary arrival and viewscreen records. The additional arrays prepare the boundary for future local-space travel without pretending that body selection already exists.

Definition validation reports:

```text
systemCount
routeCount
planetCount
starCount
habitablePlanetCount
inhabitedPlanetCount
multiPlanetSystemCount
multiStarSystemCount
localNavigationSystemCount
localDestinationCount
localRouteCount
```

For the default fixture:

```text
systemCount: 32
routeCount: 42
planetCount: 40
multiPlanetSystemCount: 2
multiStarSystemCount: 2
localNavigationSystemCount: 2
localDestinationCount: 10
localRouteCount: 10
```

## Local-system state boundary

Definition version `game.spaceNavigation.definition.v2` and state version `game.spaceNavigation.state.v2` establish the runtime boundary for local navigation without yet implementing local transit.

For Solace Reach and Vela Gate, the runtime exposes:

```text
currentLocalDestinationId
currentLocalDestination
currentLocalDestinations
currentLocalRoutes
currentLocalDestinationCount
currentLocalRouteCount
destinationLocalDestinationId
destinationLocalDestination
destinationLocalDestinations
destinationLocalRoutes
discoveredLocalDestinations
```

The current local destination determines which parent planet is projected through the legacy `currentPlanet` fields. Restoring Vela Gate at `destination.vela-gate.chiron-observatory`, for example, also restores `planet.chiron` as the current planet.

When warp arrival commits, the runtime changes both:

```text
currentSystemId
currentLocalDestinationId
```

The local destination becomes the destination system's authored `arrivalDestinationId` and is added to local discovery state.

Systems without an authored local graph receive a deterministic primary-orbit fallback. This preserves the existing thirty single-focus systems while allowing Solace Reach and Vela Gate to use explicit local topology.

No method in this patch plots or engages a local route. That operation belongs to patch two.

## Captain's Scrawl

The definition supplies:

> Congratulations, Captain, on picking the better of the two systems.

The bridge navigation panel displays the scrawl after a course is plotted and while warp travel is active. It is hidden during ordinary in-system browsing before commitment.

The runtime does not claim that one destination is mathematically or morally superior. Campaign framing must make the selected major route a just-in-time central chapter.

## Jump transaction

```text
in-system
→ course-plotted
→ warp-charging
→ in-warp
→ arriving
→ in-system
```

Arrival is an explicit commit. Only the commit changes `currentSystemId` and adds `worldTimeCost` to `elapsedWorldTime`.

The plotted route is cleared after arrival, and the destination list is rebuilt from routes adjacent to the new current system.

## Movement during warp

Engaging warp closes the navigation panel but does not clear player movement input. W/A/S/D walking and looking remain active through the travel phases.

Boarding-combat simulation remains paused during the jump so warp does not silently resolve combat while the player walks the ship.

## Viewscreen presentation

The bridge viewscreen continues to use the primary planet as the current target:

```text
warp-charging: origin planet recedes
in-warp: animated tunnel streaks move across the viewscreen
arriving: destination primary planet grows into view
arrival commit: destination primary planet becomes the normal display
```

Multiple-star and multiple-planet rendering is not implemented. The runtime exposes the data, while the viewscreen remains a single-target presentation.

## Mother-ship content

All three default projects define:

```text
terminal.bridge-navigation
openBridgeNavigationConsole
prop.console.bridge-navigation
prop.marker.bridge-navigation
```

The physical console is on the bridge deck opposite the tactical and planetary-sensor console.

## Executable proof

Run:

```powershell
python -m pytest -q `
  tests/test_space_navigation_definition_contract.py `
  tests/test_space_navigation_runtime.py `
  tests/test_game_scenario_bible_docs.py `
  tests/test_game_definition_validator_harness.py
```

The targeted tests prove:

```text
the three projects share one 32-system definition
the graph contains 42 routes and remains connected
every system has at least two neighbors
all 40 planet identities remain unique
the eight removed system ids are absent from topology and discovery state
Solace Reach and Vela Gate each expose 2 stars and 5 worlds
the selector remains flat
the first two local graphs each contain five destinations and five connected routes
local destination body references, route endpoints, arrivals, and state references resolve
runtime restoration keeps current system, local destination, and parent planet aligned
warp arrival commits the destination system's local arrival identity
the route affirmation reaches the browser presentation
Vela Gate can be plotted from Solace Reach
Meridian Prime cannot be plotted directly
arrival advances world time exactly once
W/A/S/D movement remains available during warp
```

## Destination-content boundary

A completed warp changes the active system, local arrival destination, world time, adjacent routes, body summaries, and primary-planet presentation. It does not yet let the player plot or traverse the authored local routes or run the absorbed scenarios.

Future destination loading should preserve:

```text
arrival commit
→ resolve local-space definition
→ load persistent system state
→ select a local body or corridor
→ instantiate interacting crises
→ reflect prior route and campaign consequences
```

The design authority is `pretty_docs/game-star-system-density-and-choice-contract.md`, the campaign bible, and the regional dossiers.
