# Patch-Aware Game Content Architecture

Contract: `game.patch-aware-content-architecture.v1`

This document describes how game patches should become understandable to the game runtime instead of being only source-file replacements.

## Core idea

A patch should describe both:

```text
what files change
what game-world capability is being added or modified
```

The current `new_patch.py` workflow is good for safe replacement files. The next step is to make content patches carry structured intent and acceptance checks so the runtime and tests can detect mismatches before a user gets stuck in the game.

## Patch tiers

| Tier | Name | Typical content | Risk |
| --- | --- | --- | --- |
| 1 | State patch | flags, objectives, terminal status, encounter defaults | low |
| 2 | Scene/content patch | rooms, exits, props, signs, terminals, lights | low to medium |
| 3 | Interaction patch | safe named interaction ids and handler bindings | medium |
| 4 | Runtime/source patch | engine code, renderer capability, validators | high |

Most game features should be Tier 1-3 after the runtime supports data definitions. Tier 4 should be used when the engine needs a new reusable capability.

## Patch metadata shape

Future patch bundles can include a metadata document such as:

```json
{
  "patchId": "bridge-tactical-console",
  "title": "Bridge Tactical Console",
  "tier": 2,
  "requires": ["bridge.deck", "terminal.bridge-viewscreen"],
  "adds": {
    "terminals": ["terminal.bridge-tactical"],
    "interactions": ["fireEnemyShipVolley"],
    "objectives": ["bridge.attack-raider"]
  },
  "acceptance": [
    "bridge.deck is reachable from bay.shuttle",
    "terminal.bridge-tactical is inside bridge.deck bounds",
    "terminal.bridge-tactical prompt appears in range",
    "pressing E reduces enemyShip.hull",
    "viewscreen reflects enemyShip hull state"
  ]
}
```

This metadata does not replace `new_patch.py`; it complements it.

## Acceptance checks as game contracts

Every feature patch should define user-facing success conditions.

Bad acceptance check:

```text
scene-viewer.js contains fireEnemyShipVolley
```

Good acceptance checks:

```text
player can reach the tactical console
prompt appears only when in range
pressing E changes enemy ship hull
the HUD/objective reflects the new state
the viewscreen display changes after the volley
```

The second set describes what the player experiences and what the game must prove.

## Preventing known defect classes

| Defect | Required validation |
| --- | --- |
| Visible bridge cannot be entered | room graph and movement bounds include `bridge.deck` |
| Prompt appears but E does nothing | every prompt has an interaction id and registered handler |
| Black doorway into void | every exit has modeled corridor/floor/wall coverage |
| Terminal interaction seems inert | interaction must change observable state or HUD text |
| Objective points to unreachable object | objective target must exist and be reachable from current route |
| Cutscene never exits | cutscene has completion state and timeout/escape path |

## Patch-aware development loop

```text
write patch intent
→ add or update game definition data
→ run schema validation
→ run reachability/objective/interactable validators
→ dry-run replacement zip with new_patch.py
→ apply in test repo
→ verify acceptance checks
```

## In-game patch terminal concept

Later, the game can expose this architecture through an Engineering Patch Bay or Ship Computer:

```text
Patch detected: Bridge Tactical Console
Tier: Scene/content + interaction
Dry-run: clean
Validation: bridge deck reachable; tactical console reachable; handler registered
Install: ready
```

This should remain a gameplay presentation of the same validation data, not a separate hidden system.

## Source patches remain explicit

The player-facing patch-aware system must never silently execute arbitrary downloaded code. Runtime/source patches should remain explicit repository patches applied through the existing replacement-file workflow.

## Recommended repository convention

Patch-aware content should use stable ids:

```text
room ids:      bridge.deck
exit ids:      exit.bridge-access-to-deck
terminal ids:  terminal.bridge-tactical
prop ids:      prop.bridge-viewscreen
objective ids: bridge.attack-raider
interaction ids: fireEnemyShipVolley
state keys:    enemyShip.hull
```

Stable ids let docs, tests, game definitions, HUD state, and future tools refer to the same things.
