# Mother Ship Bridge Route Plan

> **Door rule:** Mother-ship doors are never progression locks. Doors may have labels, status lights, terminals, or story prompts, but the player route remains open.

Contract: `game.mother-ship-bridge-route-plan.v1`

This document plans the rest of the mother-ship deck needed for a clear playable route from post-docking shuttle-bay control to the bridge.

## Goal

The player should be able to understand and complete this route without seeing black voids, unmodeled doorways, or misleading prompts:

```text
Mother Ship Shuttle Bay
→ Starboard Interior Access
→ Bay Operations
→ Inner Shuttle Bay Door
→ Security Checkpoint
→ Main Corridor Hub
→ Engineering Access
→ Engineering Power Console
→ Main Corridor Hub
→ Bridge Command Door
→ Bridge Access Vestibule
→ Bridge Deck
```

The route should feel like one connected ship. Every walkable square must have visible floor, walls, ceiling framing, lighting, and signage.

## Current runtime baseline

The current renderer already has a useful interior state scaffold:

| Runtime ID | Intended display | Current role |
| --- | --- | --- |
| `bay.shuttle` | Mother Ship Shuttle Bay | Arrival room and docked shuttle |
| `bay.ops` | Bay Operations | Starboard access vestibule and first terminal |
| `security.checkpoint` | Security Checkpoint | Transition from bay to the interior |
| `corridor.main` | Main Corridor Hub | Branch point for ship departments |
| `engineering.access` | Engineering Access | Required power objective |
| `medbay.stub` | Medbay Triage | Side destination / future support room |
| `science.ops.stub` | Science/Ops Lab | Side destination / future authorization room |
| `bridge.access` | Bridge Command Door | Current bridge approach placeholder |

The next implementation should not replace that model. It should extend it so `bridge.access` becomes a bridge approach area and a new final bridge room exists.

## Bridge-route zones

### 1. Starboard Interior Access

Purpose:
- make the shuttle bay clearly lead into the ship.

Must include:
- visible starboard-side opening from the bay;
- short access vestibule;
- clear floor striping from the shuttle ramp to the access;
- sign text or HUD prompt: `BAY OPERATIONS / SHIP INTERIOR`.

Implementation notes:
- keep `door.bay-access` open by default;
- do not let the access put the player into an unmodeled black area;
- keep the player facing forward into `bay.ops` after using the access.

### 2. Bay Operations

Purpose:
- teach E-key terminal interaction and open the first interior door.

Must include:
- Bay Operations terminal on the starboard wall;
- visible inner shuttle bay door ahead;
- status lights that switch from available/offline to open/online.

Progression:
- `terminal.bay-ops = online`;
- `door.bay-inner = open`;
- objective becomes `Enter the Security Checkpoint`.

Acceptance:
- the prompt only appears while the player is actually in `bay.ops`;
- the door is visibly passable after the terminal is used;
- the next room is visible beyond the door.

### 3. Security Checkpoint

Purpose:
- create a readable threshold between the bay and the rest of the ship.

Must include:
- security arch or scanner;
- checkpoint desk or side pillar;
- warning stripes and quarantine lighting;
- visible exit toward the main corridor hub.

Progression:
- first playable route can let the player open `door.security-hub` with E;
- later patches can require a scan, badge, or security terminal.

Acceptance:
- entering this area changes HUD location to `Security Checkpoint`;
- E at the checkpoint door opens the hub route;
- the hub is visible through the opening, not hidden behind a black panel.

### 4. Main Corridor Hub

Purpose:
- make the ship readable and point the player toward Engineering before Bridge.

Must include:
- four signed directions:
  - `AFT: SHUTTLE BAY`;
  - `STARBOARD: ENGINEERING`;
  - `PORT: MEDBAY / SCIENCE-OPS`;
  - `FORWARD: BRIDGE`;
- a center line or floor arrows from security to the bridge trunk;
- distinct colors for branches.

Progression:
- forward bridge route should be visibly present but available before Engineering is online;
- the active objective should point to Engineering before the bridge opens.

Acceptance:
- walking into the hub changes HUD location to `Main Corridor Hub`;
- the player can tell which way to go without reading source code;
- each branch has visible walls and floor, even if a branch is only a stub.

### 5. Engineering Access

Purpose:
- provide the required bridge activate objective.

Must include:
- engineering door from the hub;
- short engineering vestibule;
- power console;
- reactor or power conduit visual behind/near the console;
- power-state lighting change when used.

Progression:
- using `terminal.engineering-power` changes power from `emergency` to `online` or `partial`;
- bridge route changes from available to available;
- objective becomes `Return to the Bridge Command Door`.

Acceptance:
- the Engineering Power Console prompt only appears in `engineering.access`;
- using it visibly changes the ship state;
- the bridge door can be opened afterward.

### 6. Medbay and Science/Ops side rooms

Purpose:
- make the ship feel filled out while keeping the bridge route simple.

Medbay first pass:
- triage bed;
- med console;
- health/status panel;
- optional heal interaction.

Science/Ops first pass:
- ops table or sensor console;
- command-access status display;
- optional authorization token for future bridge gating.

Bridge-route rule:
- the first playable route should not require both side rooms unless the objective text says so.
- If Science/Ops becomes required, the objective chain must explicitly say: `Retrieve command authorization from Science/Ops`.

### 7. Bridge Access Vestibule

Purpose:
- replace the current `bridge.access` placeholder with a real space outside the bridge.

Recommended runtime rename path:
- keep `bridge.access` as an alias for compatibility;
- introduce `bridge.access` as the clearer new ID;
- update location labels to show `Bridge Access Vestibule`;
- keep `door.bridge` as the door from the forward trunk into this vestibule.

Must include:
- heavy command door;
- small access vestibule;
- command context terminal or wall panel;
- view into the bridge once available, if possible.

Progression options:
- simple route: Engineering power opens `door.bridge`;
- richer route: Engineering power enables the command panel, Science/Ops provides authorization, then `door.bridge-inner` opens.

For the next implementation patch, prefer the simple route so the player can finally reach the bridge.

### 8. Bridge Deck

Purpose:
- final destination for this expansion slice.

Recommended new runtime ID:
- `bridge.deck`

Must include:
- forward viewport;
- captain/command chair or central command dais;
- helm and ops consoles;
- rear door back to `bridge.access`;
- arrival objective completion.

Minimum gameplay:
- walking through the bridge door changes HUD location to `Bridge Deck`;
- objective becomes `Reach the bridge: complete` or `Use the command console`;
- pressing E at the command console reports ship status.

Acceptance:
- the bridge is a real walkable room, not the same corridor placeholder;
- the player can turn around and return to the ship;
- no combat or shuttle controls leak into bridge movement.

## Recommended coordinate extension

The existing interior uses negative Z as forward into the ship. Keep that convention.

Use the current forward trunk and bridge placeholder as the approach:

```text
corridor.trunk      x -2.55.. 2.55, z -25.85..-13.25
bridge.access       x -3.20.. 3.20, z -33.20..-25.45
bridge.deck         x -6.40.. 6.40, z -42.00..-33.20
```

The first implementation should add `bridge.deck` as a room forward of the current bridge access zone. That avoids overloading `bridge.access` as both the status-only door and the final bridge.

## Objective chain

Recommended objective IDs:

| Objective ID | Display text | Completion |
| --- | --- | --- |
| `objective.bay-ops` | Use Starboard Interior Access and bring Bay Operations online. | Bay Ops terminal used |
| `objective.enter-security` | Pass through the Inner Shuttle Bay Door to Security Checkpoint. | location becomes `security.checkpoint` |
| `objective.enter-hub` | Open the Security Checkpoint Door and enter the Main Corridor Hub. | location becomes `corridor.main` |
| `objective.restore-power` | Restore main power in Engineering Access. | Engineering console used |
| `objective.return-bridge` | Return to the forward Bridge Command Door. | player reaches `bridge.access` |
| `objective.enter-bridge` | Open the Bridge Command Door and enter the Bridge Deck. | location becomes `bridge.deck` |
| `objective.command-console` | Use the Bridge Command Console. | bridge console used |

## Door and terminal state plan

| ID | Initial state | Activate condition | Result |
| --- | --- | --- | --- |
| `door.bay-access` | `open` | none | lets player enter Bay Ops |
| `door.bay-inner` | `available` | `terminal.bay-ops = online` | opens route to Security |
| `door.security-hub` | `closed` | E-key at checkpoint | opens route to Hub |
| `door.engineering-access` | `closed` | E-key at Hub | opens Engineering |
| `terminal.engineering-power` | `offline` | E-key at Engineering console | power online and bridge activate |
| `door.bridge` | `available` | Engineering power online | opens bridge access |
| `door.bridge-inner` | `closed` or omitted in first pass | optional command panel | opens final bridge room |
| `terminal.bridge-command` | `offline` | player reaches Bridge Deck | completes bridge-arrival slice |

## Implementation patch sequence

### Patch BR-1: Security-to-hub visibility

Player-facing result:
- the black corridor beyond Bay Ops becomes a modeled security checkpoint and visible hub entrance.

Files likely touched:
- `main_computer/web/applications/scripts/scene-viewer.js`
- game tests

Acceptance:
- no walkable void between `bay.ops` and `corridor.main`;
- Security Checkpoint and Main Corridor Hub are visually distinct.

### Patch BR-2: Hub signage and branch geometry

Player-facing result:
- the player can stand in the hub and clearly see Engineering, Medbay, Science/Ops, Bridge, and Shuttle Bay directions.

Acceptance:
- hub branch floors/walls are visible;
- bridge door is visible and reachable;
- objective points to Engineering.

### Patch BR-3: Engineering power objective

Player-facing result:
- player can enter Engineering Access, use the power console, and activate the bridge route.

Acceptance:
- pressing E at Engineering changes power and objective;
- bridge door prompt changes from available to open/available.

### Patch BR-4: Bridge access vestibule

Player-facing result:
- player can walk from the hub to a modeled bridge approach instead of a placeholder.

Acceptance:
- `bridge.access`/`bridge.access` has visible geometry;
- bridge access uses correct HUD location and prompt;
- no movement bounds let the player leave the model.

### Patch BR-5: Bridge deck

Player-facing result:
- the player can finally enter a modeled bridge.

Acceptance:
- `bridge.deck` exists as a walkable room;
- command console prompt appears only on the bridge;
- objective completes or advances at the bridge console.

### Patch BR-6: Side-room polish

Player-facing result:
- Medbay and Science/Ops feel like real ship areas rather than empty branches.

Acceptance:
- both rooms have visible props and at least one harmless interaction;
- neither blocks bridge completion unless explicitly made part of the objective chain.

## Regression checks each patch should add

Add or update tests to check literal behavior in `scene-viewer.js`:

- the relevant location ID exists;
- the relevant door/terminal ID exists;
- prompt text is location-gated;
- movement bounds include the new room;
- door state never blocks traversal; geometry bounds keep players inside modeled spaces;
- objective text points to the next step;
- `node --check` passes.

## Do not do this

Avoid these failure modes:
- do not make a black rectangle stand in for a corridor;
- do not allow movement into a region before its geometry is drawn;
- do not show door prompts from the wrong location;
- do not make the player hunt for an invisible E-key target;
- do not activate the bridge without a visible objective explaining why;
- do not collapse the bridge into the existing available-door placeholder.
