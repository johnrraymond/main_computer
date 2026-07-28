# Game Runtime Patch D: Terminals and Interactables

Contract: `game.runtime-patch-D-interactables.v1`

Patch D is the fourth safe rearchitecture step for the shuttle/mother-ship game. It preserves current gameplay while moving player-facing E-key targets into data.

## Purpose

The bridge and mother-ship interior have been fragile because prompts, interaction radii, terminal ids, and handler ids lived in separate hardcoded branches. Patch D gives the runtime one explicit list of interactables so the Game Surface can answer these questions from data:

```text
what is near the player?
what prompt should be shown?
which action should E dispatch?
which room/location owns the target?
```

## Runtime change

Patch D adds `motherShipInterior.interactables` and the runtime fallback factory `shuttle3dNormalizeMotherShipInteractables`.

Each interactable includes:

```json
{
  "id": "terminal.bridge-tactical",
  "kind": "terminal",
  "label": "Bridge Tactical Console",
  "location": "bridge.deck",
  "position": [2.85, -36.7],
  "range": 1.85,
  "action": "fireBridgeTacticalConsole",
  "prompt": "Press E to fire Bridge Tactical Console."
}
```

The renderer now uses the normalized list for prompt detection and E-key dispatch. The existing actions are still implemented as safe runtime handlers; Patch E will move that dispatch table into a formal interaction registry.

## Current extracted targets

```text
door.bay-access
terminal.bay-ops
terminal.engineering-power
door.bay-inner
door.security-hub
door.engineering-access
door.medbay
door.science
door.bridge
terminal.bridge-tactical
terminal.bridge-viewscreen
```

## Behavior preserved

Patch D must not change the intended player route:

```text
dock shuttle
enter shuttle bay
use starboard interior access
walk through Bay Ops / security / corridor / engineering
reach bridge
use bridge viewscreen
use tactical console to attack the enemy ship
```

Door locking remains removed. Door interactions are informational route/status checks.

## Acceptance checks

```text
shipInteractionZones() returns normalized config.interactables
shipInteractionHint() uses the prompt from the interactable definition
interactWithShip() dispatches through the interactable action id
bridge tactical console still fires via fireBridgeTacticalConsole
bridge viewscreen still changes enemy tracking state
project metadata includes motherShipInterior.interactables
schema docs describe interactables as reachability/prompt/action targets
```

## Next patch

Patch E should formalize the action switch into an interaction registry so each interactable action maps to a named safe handler with explicit effects and tests.
