# Mother Ship Implementation Plan

Contract: `game.mother-ship-implementation-plan.v1`

This document turns the mother-ship expansion design into safe implementation milestones for the current repository.

## Current baseline

The uploaded snapshot already contains the shuttle-side game path:

```text
shuttle cockpit
→ hostile boarding defense
→ console hover + E pilot mode
→ W/S shuttle flight to mother ship
→ docking cutscene
→ shuttle-bay player-control handoff
→ in-game Shuttle Bay Twiddle System
```

The next work should build on that baseline without replacing the renderer wholesale.

## Primary implementation file

Expected main implementation target:

```text
main_computer/web/applications/scripts/scene-viewer.js
```

The current scene renderer is large. Prefer narrow additions:
- new config helpers;
- new state initializer;
- new drawing helpers;
- new interaction target helpers;
- small calls from existing movement/draw/update paths.

Avoid broad rewrites unless the renderer is intentionally split into modules.

## Project metadata files

Default projects should remain aligned:

```text
game_projects/webgl-demo/project.json
game_projects/starter-game/project.json
game_projects/new-game/project.json
```

If new metadata is added, update all three together.

## Suggested metadata namespace

Add mother-ship expansion data under:

```javascript
scene.metadata.shuttle3d.motherShipInterior
```

Suggested first shape:

```javascript
{
  enabled: true,
  startLocation: "bay.shuttle",
  debugTwiddle: true,
  regions: [...],
  doors: [...],
  terminals: [...],
  objectives: [...]
}
```

Keep renderer defaults strong enough that old projects still run if metadata is absent.

## Milestone 1: Stable post-docking first-person room

Purpose:
- prove the player can move freely in the mother ship shuttle bay without the cutscene state leaking.

Tasks:
- separate shuttle-bay gameplay camera from cutscene camera;
- keep docked shuttle visible as static scenery;
- add bay movement bounds and colliders;
- show `Mother Ship Shuttle Bay` in HUD;
- preserve Twiddle button as a safety fallback.

Acceptance:
- after docking, W/A/S/D moves the camera around the bay;
- the cutscene message is gone;
- `dockingCutsceneActive` is false;
- `bayPlayerControlActive` is true;
- no hidden console function is required.

## Milestone 2: Bay Operations terminal and inner door

Purpose:
- establish reusable in-world interactions beyond shuttle consoles.

Tasks:
- add terminal interaction target for Bay Operations;
- add door interaction target for the inner bay door;
- implement hover label and E-key use;
- add door state to runtime state;
- unlock/open the inner door when the terminal is used.

Acceptance:
- hovering terminal shows a prompt;
- pressing E on terminal changes door state;
- the player can pass through the inner door only after unlock/open;
- HUD objective updates from `Use Bay Operations` to `Enter Main Corridor`.

## Milestone 3: Corridor hub and location system

Purpose:
- turn the ship from one room into connected spaces.

Tasks:
- draw security checkpoint and main corridor hub;
- add region definitions;
- update location from camera position;
- add signs for Engineering, Medbay, Science/Ops, Bridge, Shuttle Bay.

Acceptance:
- walking through the open bay door changes HUD location;
- the player can return to the shuttle bay;
- signs are visually readable or represented by HUD hover text;
- movement bounds do not let the player escape the corridor shell.

## Milestone 4: Engineering Access and partial power

Purpose:
- add the first ship system objective.

Tasks:
- draw Engineering Access vestibule;
- add engineering power console;
- add `shipPower` state;
- change lighting/status after partial power restore.

Acceptance:
- Engineering Access is reachable from the hub;
- pressing E at the engineering console changes power from `emergency` to `partial`;
- HUD and/or lighting reflects the power change;
- objective updates after power restoration.

## Milestone 5: Bridge gate and future hooks

Purpose:
- set up the next story target without implementing the full bridge.

Tasks:
- add bridge access door/sign;
- show lock reason;
- add objective to review command lockout;
- leave bridge interior for a later milestone.

Acceptance:
- player can find the bridge door;
- pressing E explains why it is locked;
- no dead-end feels like a broken wall.

## Testing plan

### JavaScript syntax

Always run:

```bash
node --check main_computer/web/applications/scripts/scene-viewer.js
```

### Python route syntax

If `main_computer/viewport_routes_game.py` changes, run:

```bash
python -m py_compile main_computer/viewport_routes_game.py
```

### Targeted tests

Use the existing game-related tests as the minimum regression group:

```bash
python -m pytest \
  tests/test_game_editor_functional.py \
  tests/test_viewport_babylon_surface.py \
  tests/test_viewport_game_editor.py
```

If known unrelated atlas fixture failures appear, deselect only those tests and state that explicitly.

## New test expectations to add as implementation proceeds

Add assertions that:
- mother-ship interior metadata is emitted in project JSON;
- the scene runtime includes region, door, terminal, and objective defaults;
- the Bay Operations terminal unlocks the bay inner door;
- post-docking control can be forced by the in-game twiddle system;
- the cutscene cannot remain active after bay player control starts;
- bridge door lock reason appears in renderer output or runtime state.

## Risk notes

### Renderer size

`scene-viewer.js` is already large. Large unstructured edits can introduce scope bugs like an undefined callback variable. Prefer helper functions and targeted tests.

### Cutscene state leakage

The docking cutscene and shuttle-bay gameplay must remain separate. Bay gameplay should not depend on cutscene elapsed time except for the handoff trigger.

### Interaction conflicts

The E-key is used for shuttle consoles and should also be used for ship terminals/doors. The interaction resolver needs priority rules:

```text
ship terminal or door under cursor
> shuttle bay twiddle action
> shuttle console pilot station
> generic inspect prompt
```

After the player leaves the shuttle, shuttle console hover should not capture E-key interactions.

### Debug controls becoming game design

Twiddle controls are useful while building, but they are not a substitute for the actual handoff and door systems. Keep the twiddle panel visibly debug-labeled.

## Recommended next patch

The next implementation patch should do Milestone 1 and Milestone 2 together only if small. If the renderer changes become broad, split them:

1. `mother_ship_bay_stable_control_patch.zip`
2. `mother_ship_bay_ops_door_patch.zip`

The first patch should be considered clean only when dry-run succeeds and the player can move after docking without pressing Twiddle.
