# Mother Ship Systems Design

> **Door rule:** Mother-ship doors are never progression locks. Doors may have labels, status lights, terminals, or story prompts, but the player route remains open.

Contract: `game.mother-ship-systems-design.v1`

This document defines the gameplay systems needed to fill out the rest of the mother ship after docking.

## System scope

The mother ship should have systems that are understandable in gameplay and cheap to represent in the current WebGL scene renderer:

- location state;
- door state;
- ship power;
- security quarantine;
- terminals;
- objectives;
- area prompts;
- debug/twiddle controls for recovery.

The first implementation should not require a separate engine or asset pipeline.

## Runtime state model

Recommended state shape:

```javascript
shipState = {
  location: "shuttle-bay",
  power: "emergency",
  security: "quarantine",
  objectiveId: "bay-door-override",
  bayControlActive: true,
  doors: {
    "door.bay-inner": "available",
    "door.security-hub": "open",
    "door.engineering-access": "open",
    "door.medbay": "available",
    "door.science": "available",
    "door.bridge": "available"
  },
  terminals: {
    "terminal.bay-ops": "unused",
    "terminal.engineering-power": "unused"
  },
  flags: {
    dockedShuttleVisible: true,
    boardersPaused: true,
    partialPowerRestored: false
  }
}
```

Use this as renderer runtime state at first. Promote it to project JSON only when the editor needs to author or persist it.

## Location system

Location should be derived from camera position plus a small set of rectangular regions.

Each region needs:
- `id`;
- display label;
- bounds;
- optional ambient status;
- optional objective hint;
- optional safe/combat flag.

Example:

```javascript
{
  id: "corridor.hub",
  label: "Main Corridor Hub",
  bounds: { minX: -3.2, maxX: 3.2, minZ: 5.5, maxZ: 12.5 },
  safe: true,
  hint: "Follow signs to Engineering, Medbay, Science/Ops, or Bridge."
}
```

HUD should show the active location label.

## Door system

Doors are interaction targets and state gates. A door should always explain its current state.

Door states:
- `open`;
- `closed`;
- `available`;
- `powered-off`;
- `damaged`;
- `sealed`.

Door interaction result examples:

| State | On E-key |
| --- | --- |
| `open` | no-op or close if closing is supported |
| `closed` | opens |
| `available` | show status reason |
| `powered-off` | show power requirement |
| `damaged` | show repair requirement |
| `sealed` | show security requirement |

First milestone doors:
- `door.bay-inner`: available before Bay Operations terminal is used;
- `door.bridge`: available with clear command-access reason;
- other future doors visible but non-blocking stubs or available stubs.

## Terminal system

Terminals should use the same in-world hover + E interaction language as shuttle consoles.

Terminal fields:
- `id`;
- display label;
- area;
- interaction radius or bounds;
- available action;
- required state;
- result state;
- HUD text.

First milestone terminals:

### Bay Operations Terminal

Purpose:
- explains docking completion;
- activates the inner bay door;
- sets objective to `enter-main-corridor`.

Action:

```text
Use Bay Operations → door.bay-inner = open
```

### Engineering Power Console

Purpose:
- restores partial power;
- updates lighting and status;
- sets objective to `return-to-bridge-door` or `ship-status-review`.

Action:

```text
Use Engineering Power → power = partial
```

## Objective system

Objectives should be small, state-driven, and visible in the HUD.

Recommended first objectives:

| Objective ID | Display text | Completion |
| --- | --- | --- |
| `leave-shuttle` | Exit the shuttle into the bay | bay control becomes active |
| `bay-door-override` | Use Bay Operations to activate the bay door | Bay terminal used |
| `enter-main-corridor` | Enter the main corridor | location becomes `corridor.hub` |
| `restore-partial-power` | Restore partial power at Engineering Access | engineering terminal used |
| `review-command-command context` | Check the bridge access door | bridge door inspected |

## Combat rules

During the first mother-ship expansion, boarders remain paused after docking. Combat should only return once ship spaces can support movement and line-of-sight reliably.

Recommended staged return:
1. no combat in safe first slice;
2. scripted warning only;
3. one contained encounter in Security Checkpoint;
4. roaming threats in available corridors.

Until stage 3, firing can remain disabled or harmless in safe spaces.

## In-game twiddle system

The current Shuttle Bay Twiddle System should become a general **Game Systems Twiddle** panel while development continues.

Minimum visible controls:
- force shuttle-bay player control;
- show current location;
- show cutscene active/complete state;
- show active objective;
- show current door states.

Future controls:
- activate next door;
- reset to shuttle bay;
- restore partial power;
- toggle combat pause.

Rules:
- twiddle actions must use the same public renderer methods as real game state transitions;
- twiddle actions must not directly mutate deep state without updating HUD/colliders;
- tests should assert that twiddle buttons exist only when the debug system is enabled.

## HUD requirements

HUD must always answer:
- where am I?
- what should I do next?
- what is interactable under the cursor?
- why can I not pass this door?

Suggested HUD lines:

```text
LOCATION: MOTHER SHIP SHUTTLE BAY
OBJECTIVE: USE BAY OPS TO OPEN INNER DOOR
HOVER: BAY OPS TERMINAL - PRESS E
SHIP POWER: EMERGENCY
SECURITY: QUARANTINE
```

## Acceptance tests to add with implementation

When implementation begins, add tests for:

- project JSON contains mother-ship expansion metadata;
- renderer has mother-ship location state initializer;
- Bay Operations terminal activates the bay door;
- area region definitions include shuttle bay, corridor hub, and engineering access;
- shuttle-bay handoff no longer depends on cutscene camera state;
- debug/twiddle system uses in-game UI controls, not hidden browser globals;
- boarders remain paused after docking unless shipboard combat is explicitly enabled.
