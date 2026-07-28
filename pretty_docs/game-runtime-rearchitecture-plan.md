# Game Runtime Rearchitecture Plan

Contract: `game.runtime-rearchitecture-plan.v1`

This document defines Patch A for rearchitecting the current game surfaces from hand-coded scene behavior into a data-driven runtime. It applies to the shuttle game, mother-ship interior, bridge sequence, tactical systems, and future games authored in this repository.

## Problem statement

The current playable path is useful, but the implementation keeps mixing unrelated responsibilities inside the scene renderer:

```text
geometry
movement bounds
location detection
prompt discovery
E-key dispatch
terminal state
objectives
cutscene state
ship HUD text
enemy ship effects
debug/twiddle controls
```

That coupling makes the same defect pattern likely to repeat:

```text
a room is visible but not reachable
a prompt appears but the interaction handler does not change state
an objective points at an object outside movement bounds
a wall/door model disagrees with collision
a cutscene ends but player control remains in the old mode
```

The rearchitecture goal is not a rewrite. The goal is to keep the current game playable while extracting durable systems one at a time.

## Target architecture

```text
Game Runtime
  - loads game definitions
  - owns canonical game state
  - applies migrations/defaults
  - updates input, movement, location, objectives, and interactions

Scene Renderer
  - draws rooms, corridors, props, terminals, viewscreens, ships, and effects
  - reads runtime state
  - does not own progression rules

Game Definition
  - declares rooms, bounds, exits, props, terminals, interaction targets, objectives, and encounters
  - is project data, not custom renderer code

Interaction Registry
  - maps stable interaction ids to safe runtime effects
  - provides one dispatch path for the E-key and other input

Validation Layer
  - proves definitions are internally consistent
  - fails fast when content is visible but unreachable, prompted but unhandled, or referenced but missing

Patch Metadata
  - describes what a feature/content patch intends to add
  - records acceptance checks and migration expectations
```

## Responsibility split

| Layer | Owns | Must not own |
| --- | --- | --- |
| Game definition | rooms, bounds, exits, props, terminals, objectives, encounters | arbitrary JavaScript execution |
| Runtime state | current location, flags, terminal status, enemy status, active objective | hardcoded geometry |
| Interaction registry | safe named effects such as `trackEnemyShip` or `fireEnemyShipVolley` | terminal coordinates |
| Renderer | drawing primitives and visual effects | progression locks, objective resets, game-state migrations |
| Validator | reachability and consistency checks | gameplay side effects |

## Current-to-target migration

The migration should be incremental and reversible. Each patch should keep the existing playable route working.

```text
Patch A: architecture docs and first game-definition schema
Patch B: extract mother-ship state defaults into a definition object
Patch C: extract rooms, exits, and movement bounds into data
Patch D: extract terminals, prompts, and interactable positions into data
Patch E: introduce one interaction registry and route E-key dispatch through it
Patch F: add validators for reachability, prompt handlers, objectives, and spawn points
Patch G: decompose the large scene renderer into runtime/renderer/content sections or modules
Patch H: resume content expansion with data-first bridge and ship systems
```

## Engine-code patch rule

Source-code patches should add engine capability, fix runtime defects, or migrate existing behavior into cleaner boundaries. They should not be the normal way to add a single room or console after the definition layer exists.

A future bridge console should mostly be a content-data patch:

```json
{
  "terminals": [
    {
      "id": "terminal.bridge-tactical",
      "room": "bridge.deck",
      "position": [3.2, -34.8],
      "prompt": "Press E to fire tactical volley",
      "interaction": "fireEnemyShipVolley"
    }
  ]
}
```

Only the reusable `fireEnemyShipVolley` effect belongs in engine code.

## Data-first game loop

When a new game feature is added, the expected flow should become:

```text
declare content
→ validate content
→ render content
→ interact through registry
→ update canonical state
→ verify acceptance checks
```

This should replace the current flow:

```text
edit renderer in several places
→ hope geometry, movement, prompts, and state all agree
→ debug in browser after applying
```

## Non-goals for Patch A

Patch A does not change runtime gameplay, movement, geometry, UI, or interaction behavior. It only adds design documents and the first schema artifact that future patches can implement against.

## Patch A acceptance checks

Patch A is acceptable when:

```text
the architecture plan exists
the human-readable schema guide exists
the patch-aware content architecture guide exists
a machine-readable game-definition schema exists
the design index links to the new architecture documents
no runtime source or project behavior files are modified
new_patch.py --dry-run succeeds from a clean snapshot
```
