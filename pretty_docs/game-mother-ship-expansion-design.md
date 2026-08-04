# Mother Ship Expansion Design

> **Door rule:** Mother-ship doors are never progression locks. Doors may have labels, status lights, terminals, or story prompts, but the player route remains open.

Contract: `game.mother-ship-expansion-design.v1`

This document defines the design target for expanding the current **Shuttle Boarding Defense** game after the shuttle docks with the mother ship. It is intentionally a design document, not implementation proof. Current implemented scope ends at shuttle docking, shuttle-bay arrival, and first-person handoff.

## Design goal

After the player docks, the game should stop feeling like a single-room shuttle defense and become an explorable starship interior. The mother ship is a compact but believable vessel with connected decks, stations, crew spaces, systems, hazards, and mission objectives.

The first playable milestone should answer one question clearly:

```text
Can the player leave the shuttle bay, walk through the ship, understand where to go next, and interact with ship systems?
```

## Player fantasy

The player is a cadet who survived boarding attackers, flew the shuttle home, and now enters a larger ship that is damaged, partially available down, and still under threat. The mother ship is not just scenery. It should be a place the player can learn, repair, defend, and eventually command.

## Core loop after docking

```text
Arrive in shuttle bay
→ regain first-person character control
→ find the next accessible compartment
→ inspect terminals, doors, and signs
→ solve local ship-system problems
→ activate new compartments
→ recover ship status
→ advance toward bridge/engineering objectives
```

Combat can return later, but the first expansion should prioritize navigation, readable spaces, and interaction reliability.

## Design pillars

### 1. The ship is readable

Every area needs a clear role, visual identity, and navigational cue. The player should know whether they are in a bay, corridor, engineering room, medbay, or command space before reading UI text.

### 2. Doors are gameplay boundaries

Doors should not be decorative only. A door can be open, available, damaged, powered off, sealed by security, or waiting on a nearby terminal. Door state should communicate why the player cannot pass.

### 3. Systems are local first, global later

A terminal in engineering should affect engineering first. A bridge console can later provide global status. This avoids making every terminal a magic all-purpose button.

### 4. The shuttle remains an anchor

The docked shuttle is the player's arrival point and fallback landmark. The shuttle bay should remain accessible and recognizable after the cutscene is over.

### 5. Debug recovery belongs inside the game

The in-game Shuttle Bay Twiddle System should evolve into a general Game Systems panel for development recovery and state inspection. Debug escape hatches should be visible and safe in test builds rather than hidden browser console rituals.

## Suggested act structure

### Act 1: Arrival and orientation

The player exits the shuttle into the Mother Ship Shuttle Bay. The nearby bay control terminal gives a status summary:

```text
Docking complete.
Bay pressure stable.
Inner corridor door available through security quarantine.
Manual override available at Bay Operations.
```

Player goals:
- walk around the shuttle bay;
- use a nearby bay terminal;
- open the inner bay door;
- enter the main corridor.

### Act 2: Ship systems recovery

The player reaches a corridor hub where several paths are visible but not all are accessible. Engineering and medbay are the best first destinations because they teach system repair and resource restoration.

Player goals:
- restore partial power in engineering;
- use medbay to recover health or scan status;
- clear one bavailable corridor.

### Act 3: Command access

The bridge is visible as a destination and remains reachable as the ship is expanded. The player must restore enough ship systems to gain access.

Player goals:
- collect or authorize command access;
- reach the bridge;
- read mission status;
- choose the next major objective.

## First playable slice

The minimum useful slice of the mother ship should include:

| Area | Required for slice | Purpose |
| --- | --- | --- |
| Shuttle Bay | Yes | Arrival point, docked shuttle, first movement proof |
| Bay Operations Alcove | Yes | First terminal and door override |
| Main Corridor Hub | Yes | Navigation proof and future branch point |
| Engineering Access | Yes | First systems objective |
| Medbay Door or Signage | Optional visible stub | Shows future expansion without needing full room |
| Bridge Door or Signage | Optional visible stub | Establishes final direction |

## Interaction vocabulary

Use a small stable vocabulary before adding more mechanics:

| Interaction | Input | Expected behavior |
| --- | --- | --- |
| Inspect | Mouse over + E | Show object status or label |
| Use terminal | Mouse over terminal + E | Open a small in-game system panel or trigger local action |
| Open door | Mouse over door + E | Open if allowed; otherwise show status reason |
| Force control twiddle | T or visible button | Restore first-person player control in test/debug builds |
| Fire phaser | Click/Space/F | Disabled in safe ship spaces until combat returns |

## Ship-state vocabulary

Recommended global ship state fields:

```javascript
shipLocation: "shuttle-bay" | "main-corridor" | "engineering" | "medbay" | "bridge"
shipPower: "emergency" | "partial" | "nominal"
securityState: "quarantine" | "limited" | "cleared"
docked: true | false
bayPlayerControlActive: true | false
activeObjectiveId: string
availableDoors: string[]
completedObjectives: string[]
```

The renderer can keep these in its own runtime state first, then promote them to project metadata only when editor persistence is needed.

## Objective sequence for the first expansion

1. **Docking Complete**  
   Player exits the shuttle into the shuttle bay.

2. **Bay Door Override**  
   Player uses the Bay Operations terminal to activate the interior door.

3. **Enter Main Corridor**  
   Player walks out of the bay and sees directional signs.

4. **Restore Emergency Power**  
   Player reaches Engineering Access and interacts with a power console.

5. **Return Ship Status**  
   A ship-status display confirms which systems are now available.

## Visual language

| Element | Visual cue |
| --- | --- |
| Walkable floor | broad cool-gray panels with low edge strips |
| Status-only door | amber/red side lights and readable label |
| Open door | brighter cyan/green frame light |
| Usable terminal | cyan screen glow, hover outline, prompt text |
| Critical console | stronger pulsing edge light |
| Bavailable path | visible obstruction plus reason in HUD |
| Safe area | stable lighting and no combat warning |
| Hazard area | flicker, sparks, alarm strip, fog or vapor |

## Non-goals for the first design pass

The first mother-ship expansion does not need:
- a fully modeled multi-floor ship;
- inventory;
- NPC crew simulation;
- complex combat encounters;
- save/load migration;
- editor UI for every ship-room property.

Those can be planned after the ship has a stable first-person navigation and interaction base.

## Acceptance criteria

The first implementation based on this design is acceptable when:

- the player can leave the shuttle bay under normal controls without a debug twiddle;
- at least one door and one terminal are interactable through the same in-world interaction pattern;
- the ship has at least three distinct connected spaces;
- HUD/status text names the current area;
- movement bounds and colliders match the visible room geometry closely enough that the player does not walk through key walls;
- tests assert the presence of the mother-ship areas, the handoff state, and the first objective chain.
## Campaign expansion after command access

Reaching the bridge is no longer the terminal design target. Once the player can use the physical navigation console, the mother ship becomes the persistent campaign platform for the thirty-two-system graph and its dense multi-world destinations. The extended loop is:

```text
resolve or survive the current shipboard problem
→ reach the bridge
→ inspect adjacent systems and route conditions
→ choose a destination for strategic reasons
→ remain mobile inside the ship during warp
→ arrive in a system with its own physical structure, internal worlds, and active crisis
→ make a decision that changes persistent regional state
→ return to the mother ship with consequences, allies, evidence, or damage
```

The system-level content is specified by `pretty_docs/game-forty-system-scenario-bible.md` and its five regional dossier documents. Those documents should guide destination geometry, traffic, faction dialogue, mission state, and route consequences. They are design authority only until implemented and tested.

