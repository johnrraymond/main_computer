# Mother Ship Feature Patch Series

> **Door rule:** Mother-ship doors are never progression locks. Doors may have labels, status lights, terminals, or story prompts, but the player route remains open.

Contract: `game.mother-ship-feature-patch-series.v1`

This document breaks the mother-ship expansion into a practical sequence of implementation patches. Each patch should be built from the latest uploaded snapshot, packaged as replacement files for `new_patch.py`, and kept small enough that runtime defects can be isolated quickly.

## Current baseline

The current game surface includes:

```text
shuttle cockpit
→ boarding defense
→ console hover + E pilot mode
→ W/S shuttle flight to the mother ship
→ docking cutscene
→ mother-ship shuttle-bay handoff
→ in-game Shuttle Bay Twiddle System
```

The next patches should not rework that path unless a defect blocks progression. The first expansion goal is to let the player leave the shuttle bay, explore connected ship spaces, interact with ship systems, and understand the next objective.


## Architecture rework track

The game has outgrown the one-renderer-knows-everything model. Before large new room or system additions, use this architecture track to make future ship features data-driven and easier to verify.

| Patch | Purpose | Runtime behavior change |
| --- | --- | --- |
| A. Architecture docs and schema | Add runtime architecture docs, patch-aware content design, and `game-definition.v1` schema | None |
| B. State defaults extraction | Move mother-ship runtime defaults into one definition object | Should preserve behavior |
| C. Rooms and movement extraction | Move room ids, names, bounds, exits, and spawns into data | Should preserve behavior |
| D. Interactable extraction | Move terminals, prompts, radii, and objective targets into data | Should preserve behavior |
| E. Interaction registry | Route E-key handling through named safe handlers | Should preserve behavior with cleaner dispatch |
| F. Definition validators | Add tests for reachability, prompt handlers, objective targets, and spawn placement | No gameplay change |
| G. Renderer decomposition | Split drawing primitives from gameplay state and interaction logic | Should preserve behavior |

Each architecture patch should be built from the latest uploaded snapshot, packaged for `new_patch.py`, and verified with exact dry-run when possible.

## Patch series overview

| Patch | Working name | Player-facing result |
| --- | --- | --- |
| 1 | Interior State Scaffold | The game has a real mother-ship interior state model behind the bay. |
| 2 | Bay Ops Terminal + Inner Door | The player can use a bay terminal to open the first ship door. |
| 3 | Security Checkpoint + Corridor Hub | The player can leave the bay and walk into a connected corridor hub. |
| 4 | Reusable Door/Terminal System | Doors and terminals share one consistent hover/E-key interaction path. |
| 5 | Engineering Access + Emergency Power | The player restores partial ship power from Engineering Access. |
| 6 | Ship Signage + Objective Guidance | The ship becomes readable through signs, HUD location, and objective prompts. |
| 7 | Medbay and Science/Ops Stubs | Side destinations exist with simple interactions and future depth. |
| 8 | Bridge Route + Command Context | The player can reach the bridge door and learn why command is still available. |
| 9 | Threat Return in Controlled Spaces | Boarders or hazards can return outside the shuttle bay without breaking exploration. |
| 10 | Ship State Persistence Hooks | Door, terminal, power, and objective state can be serialized for later editor/save work. |
| 11 | Renderer Decomposition | Mother-ship helpers move out of the fragile monolithic scene path where safe. |
| 12 | Playable Slice Polish | The full bay-to-engineering-to-bridge-gate slice is smoothed and regression-tested. |

## Patch 1: Interior State Scaffold

Purpose:
- add a stable runtime model for mother-ship interior gameplay before adding more geometry.

Expected changes:
- add `scene.metadata.shuttle3d.motherShipInterior` defaults to project JSON;
- add renderer fallback defaults when project metadata is absent;
- add `shipState` with location, power, security, doors, terminals, objectives, and flags;
- add a small HUD/status line for ship location and objective;
- keep the existing Shuttle Bay Twiddle System visible and functional.

Primary files:
- `main_computer/web/applications/scripts/scene-viewer.js`
- `main_computer/viewport_routes_game.py`
- `game_projects/webgl-demo/project.json`
- `game_projects/starter-game/project.json`
- `game_projects/new-game/project.json`
- game-related tests

Acceptance checks:
- old projects without `motherShipInterior` still render;
- new default projects expose interior metadata;
- after docking, runtime state says the player is in `bay.shuttle`;
- no existing shuttle/docking control path regresses.

## Patch 2: Bay Ops Terminal + Inner Door

Purpose:
- establish the first non-shuttle in-world interaction.

Expected changes:
- draw a Bay Operations alcove in the shuttle bay;
- add a Bay Ops terminal interaction target;
- add the inner bay door as a visible stateful object;
- pressing E at the terminal activates or opens the inner door;
- objective changes from `Use Bay Operations` to `Enter Main Corridor`.

Primary files:
- `scene-viewer.js`
- game project JSON files if terminal/door metadata is authored there;
- tests that assert Bay Ops terminal, inner door, and objective text exist.

Acceptance checks:
- hovering the terminal shows an E-key prompt;
- pressing E changes door state;
- collision keeps players in modeled halls while door status remains non-blocking;
- boarders remain paused in the bay.

## Patch 3: Security Checkpoint + Corridor Hub

Purpose:
- turn the post-docking game from one room into a connected ship.

Expected changes:
- add a short security checkpoint beyond the bay inner door;
- add a main corridor hub with clear forward/left/right destinations;
- add simple region bounds that update HUD location;
- add collision so the player cannot walk outside the corridor shell;
- allow returning to the shuttle bay.

Primary files:
- `scene-viewer.js`
- tests for region labels and bounds definitions;
- optionally project JSON if region metadata is authored.

Acceptance checks:
- crossing the door updates location from `Mother Ship Shuttle Bay` to `Security Checkpoint`;
- walking farther updates location to `Main Corridor Hub`;
- hub signs point toward Engineering, Medbay, Science/Ops, Bridge, and Shuttle Bay;
- player can return to the bay without re-entering the cutscene.

## Patch 4: Reusable Door/Terminal System

Purpose:
- reduce future scope bugs by consolidating repeated interaction logic.

Expected changes:
- centralize hover target selection for consoles, doors, terminals, and diagnostic/twiddle controls;
- add a reusable interaction result structure;
- make prompt text derive from the active target instead of hand-coded branches;
- preserve current shuttle console behavior.

Primary files:
- `scene-viewer.js`
- focused tests for no duplicate E-key interaction paths.

Acceptance checks:
- shuttle consoles still enter pilot mode;
- Bay Ops terminal still opens the inner door;
- status-only doors show reasons;
- E-key never triggers a shuttle console after the player has left the shuttle.

## Patch 5: Engineering Access + Emergency Power

Purpose:
- add the first real ship-system objective.

Expected changes:
- add Engineering Access geometry reachable from the corridor hub;
- add an Engineering Power console;
- add `shipPower: "emergency" | "partial"` state;
- after using the console, change lighting/status lines and update the objective.

Primary files:
- `scene-viewer.js`
- project JSON if the console/objective is metadata-driven;
- tests for power state and objective text.

Acceptance checks:
- the Engineering Access area is reachable from the hub;
- pressing E at the console changes power to `partial`;
- HUD confirms partial power;
- Bridge route remains visible but the bridge status reason changes after power is restored.

## Patch 6: Ship Signage + Objective Guidance

Purpose:
- make the ship readable without requiring the player to guess where to go.

Expected changes:
- add text/sign blocks or HUD labels for each major destination;
- add objective hints when the player enters each region;
- add available-door explanations for future areas;
- include short “return to shuttle bay” guidance.

Primary files:
- `scene-viewer.js`
- CSS only if additional overlay styling is needed;
- tests for core label strings.

Acceptance checks:
- the player can identify each branch in the hub;
- all status-only doors explain their door state;
- current objective changes when the player completes Bay Ops and Engineering tasks.

## Patch 7: Medbay and Science/Ops Stubs

Purpose:
- create believable destinations without expanding scope into full subsystems yet.

Expected changes:
- add visible medbay and science/ops entrances off the hub;
- allow short entry vestibules or status-only doors with clear explanations;
- add one safe interaction per side destination, such as a medbay status panel or science sensor note.

Primary files:
- `scene-viewer.js`
- tests for stub locations and prompts.

Acceptance checks:
- each side branch feels intentional, not like missing geometry;
- the player can inspect at least one object in each branch;
- no branch traps the player.

## Patch 8: Bridge Route + Command Context

Purpose:
- set the next story target while keeping the bridge interior for a later feature.

Expected changes:
- add the bridge access door beyond the corridor hub;
- add a command context prompt;
- make the status reason depend on prior objectives, such as power restored but command authorization missing;
- add a clear “next patch” hook.

Primary files:
- `scene-viewer.js`
- project JSON/objective tests if metadata-driven.

Acceptance checks:
- the bridge door is reachable;
- pressing E explains the route status;
- after restoring partial power, the explanation changes;
- the player is directed toward a future command authorization objective.

## Patch 9: Threat Return in Controlled Spaces

Purpose:
- reintroduce danger without invalidating the safe arrival/bay-control work.

Expected changes:
- keep shuttle bay safe after docking;
- add hazards or limited attackers in specific non-bay regions;
- make combat pausable or bounded so interactions remain usable;
- keep Twiddle controls able to recover the player.

Primary files:
- `scene-viewer.js`
- tests for safe bay and threat-enabled regions.

Acceptance checks:
- no attacker spawns in `bay.shuttle`;
- threats only activate in configured regions;
- interacting with terminals remains reliable;
- player damage/health behavior is visible in the HUD.

## Patch 10: Ship State Persistence Hooks

Purpose:
- prepare for editor/save support without requiring full persistence immediately.

Expected changes:
- isolate serializable interior state from render-only transient state;
- define a compact save payload for location, doors, terminals, power, security, and objectives;
- expose a safe reset path for tests and twiddle recovery;
- do not add browser storage unless explicitly scoped.

Primary files:
- `scene-viewer.js`
- `viewport_routes_game.py` only if emitted project data changes;
- tests for state initialization/reset.

Acceptance checks:
- resetting the game resets interior state predictably;
- serializable state excludes canvas-only objects/functions;
- default projects still load cleanly.

## Patch 11: Renderer Decomposition

Purpose:
- reduce the risk of more scope errors in `scene-viewer.js`.

Expected changes:
- move mother-ship constants/helpers into a separate script only if the app already supports loading it safely;
- otherwise create clear helper sections inside `scene-viewer.js`;
- keep all public behavior unchanged.

Primary files:
- `scene-viewer.js`
- possibly a new `main_computer/web/applications/scripts/shuttle-mother-ship.js` if loader wiring is proven safe;
- tests to ensure the WebGL app includes any new script.

Acceptance checks:
- no behavior changes beyond refactor;
- all previous feature tests still pass;
- script load order is explicit and tested.

## Patch 12: Playable Slice Polish

Purpose:
- stabilize the first full mother-ship slice as a stable demo.

Expected changes:
- tune movement bounds, camera height, prompts, and objective text;
- improve visual separation between bay, checkpoint, hub, engineering, and bridge gate;
- remove temporary debug-only wording while keeping the in-game Twiddle recovery system available;
- add a concise README or design note for the playable slice.

Primary files:
- `scene-viewer.js`
- CSS if HUD/prompt polish is needed;
- game tests and docs.

Acceptance checks:
- a fresh player can complete: dock → exit shuttle → open bay door → reach hub → restore partial power → inspect bridge access;
- no hidden browser-console helper is needed;
- `node --check`, targeted game tests, and `new_patch.py --dry-run` pass.

## Recommended patch order

Build the next implementation patches in this order:

```text
1. Interior State Scaffold
2. Bay Ops Terminal + Inner Door
3. Security Checkpoint + Corridor Hub
4. Reusable Door/Terminal System
5. Engineering Access + Emergency Power
6. Ship Signage + Objective Guidance
7. Medbay and Science/Ops Stubs
8. Bridge Route + Command Context
9. Threat Return in Controlled Spaces
10. Ship State Persistence Hooks
11. Renderer Decomposition
12. Playable Slice Polish
```

If a runtime bug appears, insert a narrow corrective patch immediately after the bug is observed. Do not stack broad feature work on top of a broken baseline.

## Standard verification for every patch

Run these checks when feasible:

```bash
node --check main_computer/web/applications/scripts/scene-viewer.js
python -m py_compile main_computer/viewport_routes_game.py
python -m pytest tests/test_game_editor_functional.py tests/test_viewport_babylon_surface.py tests/test_viewport_game_editor.py
python new_patch.py <patch.zip> --dry-run
```

When a known unrelated test is deselected, record the exact deselection and reason in the patch response.

## Packaging rule

Each implementation patch should be a replacement-file artifact that assumes the latest uploaded snapshot as its source state. Raw snapshot mode does not infer deletions from omitted files, so deletion semantics must be explicit if a future patch removes a file.


## Bridge-route implementation addendum

For bridge-reaching work, use the focused route document:

```text
pretty_docs/game-mother-ship-bridge-route-plan.md
```

That route turns the broad patch series into this concrete player path:

```text
bay.shuttle
→ bay.ops
→ security.checkpoint
→ corridor.main
→ engineering.access
→ corridor.main
→ bridge.access
→ bridge.deck
```

### Bridge Route Patch BR-1: Security-to-hub visibility

Purpose:
- make the corridor beyond Bay Operations visibly connect to Security Checkpoint and the Main Corridor Hub.

Acceptance checks:
- the player never walks into a black void;
- every walkable position has modeled floor, walls, and ceiling framing;
- prompts only appear in the correct location.

### Bridge Route Patch BR-2: Hub signage and branches

Purpose:
- make the Main Corridor Hub readable before adding more objectives.

Acceptance checks:
- signs clearly point to Shuttle Bay, Engineering, Medbay, Science/Ops, and Bridge;
- the Bridge direction is visible and reachable;
- Engineering is clearly the active route.

### Bridge Route Patch BR-3: Engineering activates bridge

Purpose:
- make Engineering Access the required action that enables bridge access.

Acceptance checks:
- Engineering Power Console changes ship power state;
- objective updates to return to the Bridge Command Door;
- Bridge Command Door state changes from available to open/available.

### Bridge Route Patch BR-4: Bridge access vestibule

Purpose:
- replace the current bridge placeholder with a real approach room.

Acceptance checks:
- `bridge.access` remains compatible or aliases to `bridge.access`;
- the player can stand in a modeled vestibule outside the bridge;
- bridge prompts do not appear from the hub unless the player is near the door.

### Bridge Route Patch BR-5: Bridge deck

Purpose:
- create the final walkable bridge destination.

Acceptance checks:
- `bridge.deck` exists as a new region;
- entering it updates the HUD to `Bridge Deck`;
- a Bridge Command Console interaction completes or advances the objective.

### Bridge Route Patch BR-6: Side-room polish

Purpose:
- make Medbay and Science/Ops feel intentional without blocking the bridge route.

Acceptance checks:
- both side rooms have basic modeling and one simple interaction;
- route-to-bridge completion still works if the player ignores side rooms unless a later objective explicitly requires them.
