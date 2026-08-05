# Game AI Patch 10A — Character Policy Foundation and Playable Characters

Contract: `game.ai.patch-10-a.character-policy-foundation.v1`

Status: constrained implementation against a snapshot that contains AI-9.4.1 but not AI-9.5 repository campaign saves.

## Objective

Create a stable in-world character decision boundary before adding actual model calls.

```text
stable character identity
→ controlled perception
→ policy chooses one authored action
→ validator checks legality
→ game-owned executor applies effects
→ receipt records the result
→ deterministic fallback keeps play running
```

The model or remote service never receives authority to mutate the game directly.

## Playable entities

The WebGL demo now defines:

```text
enemy.raider-boarder-01
npc.engineering-officer-01
ship.raider-01
```

`ship.raider-01` is the stable vessel identity for the existing visible `alien-raider` scene object.

### Raider Boarder

The enemy can:

- patrol authored points;
- detect the player within a bounded perception range;
- request support from `ship.raider-01`;
- move toward the player;
- attack only when visible, in range, and off cooldown;
- take cover after recent damage;
- retreat when health drops below the authored threshold.

The player phaser now targets this stable enemy record. Damage, defeat, support requests, decisions, and movement are recorded in character receipts.

### Engineering Officer Mara Venn

The NPC becomes active after the mother-ship handoff. She can:

- hold the engineering station;
- restore main power through the existing ship-state method;
- warn the player about the boarder;
- take cover when a hostile is close;
- remember that the player protected her;
- follow the player after that protection memory is established.

Her wording is authored. The policy does not generate unrestricted dialogue.

## Stable policy interface

Every policy implements:

```text
chooseAction(perception)
```

The browser runtime includes:

```text
DeterministicCharacterPolicy
RemoteCharacterPolicy
current()
registerPolicy(policyId, policy)
setCharacterPolicy(characterId, policyId)
```

A remote policy may return synchronously or through a promise. Promise-based decisions never block the render loop.

Expected result:

```json
{
  "schema": "game.characterAI.policyResult.v1",
  "requestId": "enemy.raider-boarder-01:5:2600",
  "characterId": "enemy.raider-boarder-01",
  "actionId": "take_cover",
  "targetId": "cover.shuttle.port-console",
  "rationale": "recent incoming fire"
}
```

Only the structured fields are considered. The rationale is bounded diagnostic text and cannot cause effects.

## Controlled perception

A policy receives a small `game.characterAI.perception.v1` envelope containing:

- the character’s identity, faction, health, position, and current action;
- whether the player is visible, plus distance, health, and position;
- the nearest visible hostile for an NPC;
- one recommended authored cover point;
- one patrol target;
- one repair target;
- ship power, security, current system, and known vessel identities;
- recent damage, warning, support, repair, and protection flags;
- the character’s legal action IDs.

It does not receive the full project or arbitrary mutable world state.

## Validation and fallback

The validator rejects:

- unknown action IDs;
- mismatched characters or request IDs;
- stale asynchronous results;
- attacks without visibility, range, or cooldown;
- movement or warnings without player visibility;
- missing cover;
- repair attempts outside range or after power is online;
- repeated support calls;
- support targets that are not authored vessel records.

When a policy throws, times out, remains pending, returns malformed data, or chooses an illegal action, the deterministic policy supplies the next safe action.

No timers or polling loops were added. The renderer’s normal frame updates provide decision opportunities.

## Execution boundary

The runtime owns character state and returns typed effects. The renderer applies only recognized effects:

```text
damage-player
repair-ship-power
character-message
support-requested
```

Movement is bounded by the existing playfield and collision fixtures. The renderer, not the policy, applies phaser damage and ship-state changes.

## Persistence

Character state uses project-scoped local storage:

```text
main-computer.character-ai.state.v1:<project-id>
```

It preserves:

- stable identity;
- health and status;
- position;
- selected policy;
- current action and target;
- decision count;
- memories;
- receipts;
- deterministic sequence.

Browser-clock cooldowns are rebased after restoration so a previous page’s `performance.now()` value cannot freeze a character.

The runtime also exports:

```text
game.characterAI.campaignExtension.v1
```

That extension is ready to be inserted into a repository campaign save, but the supplied source snapshot does not contain AI-9.5’s repository save routes. Repository persistence is therefore explicitly deferred.

## Player presentation

The Game Surface:

- renders the boarder and engineering officer as distinct vertex-built bodies;
- shows health bars above both characters;
- reports names, health, and current actions in the existing HUD;
- counts the stable boarder in combat totals;
- disables anonymous auto-spawning while the character runtime owns the encounter;
- permits phaser combat after docking while an active character enemy remains.

## Deterministic proof

The focused proof establishes this enemy sequence:

```text
call support
→ move toward player
→ move toward player
→ attack player
→ take cover after damage
→ retreat at low health
```

The NPC proof establishes:

```text
mother-ship phase
→ restore engineering power
→ warn nearby player
```

The external-policy proof establishes:

```text
illegal result
→ rejected
→ deterministic fallback

promise-based legal result
→ frame continues with fallback
→ result is accepted at the next decision boundary
```

## Files changed

```text
game_projects/webgl-demo/project.json
main_computer/web/applications.html
main_computer/web/applications/scripts/character-ai-runtime.js
main_computer/web/applications/scripts/scene-viewer.js
main_computer/web/applications/styles/game-editor.css
tests/test_character_ai_runtime.py
pretty_docs/game-ai-patch-10-a-character-policy-and-playable-characters.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Boundaries

Not included:

- a real HTTP or local-model call;
- free-form generated dialogue;
- squad coordination beyond one support receipt;
- navigation or tactics for the enemy ship;
- repository-backed character persistence;
- camera, combat, or arbitrary scene-state campaign saves;
- additional characters;
- learned policies.

## Next bounded slice

After applying or restoring AI-9.5, the next safe slice is:

```text
add characterAI campaign extension to repository saves
→ restore exact character sequence and memory
→ attach one opt-in remote policy to Mara Venn
→ enforce timeout and deterministic fallback
→ compare the remote result with the same saved replay boundary
```
