# Game Runtime Patch R — Validator Test Harness

## Purpose

Patch R adds a direct project-JSON validator harness for the mother-ship interior architecture line.

The earlier runtime validator catches definition problems while the WebGL scene is being normalized. This patch adds a Python-side regression test that reads the authored project JSON directly, before the browser runtime or route handlers can smooth over mismatches.

## Scope

Patch R verifies the current default project definitions in:

- `game_projects/webgl-demo/project.json`
- `game_projects/starter-game/project.json`
- `game_projects/new-game/project.json`

The harness checks the nested `scenes[].metadata.shuttle3d.motherShipInterior` definition used by the current shuttle-to-mother-ship route.

## Validation checks

The new test validates that:

- all room ids are unique;
- room bounds are finite and inside mother-ship movement bounds;
- exits resolve to known room ids or location aliases;
- the room graph reaches every room from `initialLocation`;
- `bridge.deck` is reachable from the shuttle-bay start;
- objectives point to known locations;
- doors point to known locations and remain traversal-open;
- interactables are inside their declared room/location bounds;
- interactables have prompts, ranges, actions, and handlers;
- interactions declare `changesState`, `successStatus`, and valid `nextObjective` metadata;
- terminals have either a matching hotspot or a visible prop;
- props point to known rooms and valid targets;
- data-defined display props use a supported display program;
- the bridge viewscreen is a data-defined `enemyShipTactical` display;
- spawns are inside their declared room and playable movement bounds.

## Corridor location aliases

The current ship definition intentionally uses `corridor.main` as a location alias for more than one corridor segment. Patch R updates validation logic so location aliases can resolve to all matching rooms during definition checks, while preserving the existing first-match helper for compatibility.

Point-bearing content such as interactables, props, and spawns is now validated against the matching room that actually contains the point. This avoids false failures for authored corridor trunk content and catches prompts placed outside every room that declares the same location alias.

## Corrective content alignment

The validator harness exposed one live data mismatch:

```text
door.science
location: corridor.main
old position: [-3.25, -25.0]
```

That point sat outside both rooms that declare the `corridor.main` location alias. Patch R moves the hotspot inward to the corridor trunk edge:

```text
new position: [-2.45, -25.0]
```

This keeps the Science/Ops route visible and inspectable without turning the door into a lock.

## Non-goals

Patch R does not add new gameplay content.

Patch R does not change the no-locked-door rule.

Patch R does not move rendering code into separate browser modules. That remains a later renderer split task.
