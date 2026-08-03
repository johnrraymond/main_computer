# Game Warp Navigation Runtime

## Status

Warp navigation is executable in the browser game surface.

The game loads `project.metadata.spaceNavigation`, validates the forty-system graph, exposes only directly connected destinations, and executes a deterministic five-phase jump transaction.

## Runtime boundary

The graph engine is isolated in:

```text
main_computer/web/applications/scripts/space-navigation-runtime.js
```

The module is browser-safe and CommonJS-loadable for deterministic Node tests. It owns:

```text
definition validation
current-system state
adjacent-destination projection
course plotting
warp engagement
phase progression
arrival commit
world-time advancement
```

`scene-viewer.js` owns physical bridge-console access, HUD updates, player movement, and the browser-side warp presentation. It does not infer routes from map coordinates.

## Player operation

Navigation is available only through the physical controls on the mother-ship bridge:

```text
Reach Bridge Navigation Console
→ stand within its interaction range
→ press E
→ select an adjacent system
→ plot course
→ engage warp
```

There is no global `N` shortcut and no floating `NAV` button. Moving away from the console closes an open navigation session. Plotting, clearing, and engaging a course all re-check access to the physical bridge controls.

The console provides:

```text
Adjacent destination selector
Plot Course
Engage Warp
Current system
Travel phase
World time
Travel progress
```

At the initial system, Solace Reach, the legal destinations are Pax and Vela Gate. Meridian Prime is not directly connected and must be rejected.

## Jump transaction

A successful jump follows:

```text
in-system
→ course-plotted
→ warp-charging
→ in-warp
→ arriving
→ in-system
```

The runtime records the origin, destination, active route, start time, end time, and pending world-time cost. Arrival is an explicit commit. Only that commit changes `currentSystemId` and adds `worldTimeCost` to `elapsedWorldTime`.

The plotted route is cleared after arrival. The destination list is then rebuilt from routes originating at the new current system.

## Movement during warp

Engaging warp closes the navigation panel but does not clear player movement input. Normal W/A/S/D walking and looking remain active through `warp-charging`, `in-warp`, and `arriving`.

Boarding-combat simulation remains paused during the jump so warp does not silently resolve combat while the player walks the ship.

## Viewscreen presentation

The bridge viewscreen is the authoritative visual representation of travel.

During normal in-system operation it shows the current system planet. During warp it switches to a live transit program:

```text
warp-charging: origin planet recedes and star motion begins
in-warp: animated tunnel streaks move across the viewscreen
arriving: destination planet grows into view
arrival commit: destination planet becomes the normal system display
```

The former full-screen warp tunnel has been removed. A small phase readout remains as a HUD status indicator, but the movement itself is visible on the physical bridge viewscreen so the player can walk around the ship and observe it from different positions.

## Mother-ship content

All three default projects define:

```text
terminal.bridge-navigation
openBridgeNavigationConsole
prop.console.bridge-navigation
prop.marker.bridge-navigation
```

The physical console is on the bridge deck opposite the planetary sensor console.

## Executable proof

Run:

```powershell
python -m pytest -q `
  tests/test_space_navigation_definition_contract.py `
  tests/test_space_navigation_runtime.py `
  tests/test_game_definition_validator_harness.py
```

The runtime tests prove:

```text
Solace Reach starts with exactly two adjacent destinations.
Vela Gate can be plotted.
Meridian Prime cannot be plotted directly.
Warp reaches the in-warp phase.
Arrival commits Vela Gate as the current system.
World time advances by four exactly once.
The return route to Solace Reach becomes available.
Navigation has no global keyboard or floating-button bypass.
Warp does not suppress W/A/S/D movement.
The bridge viewscreen owns the animated transit display.
```
