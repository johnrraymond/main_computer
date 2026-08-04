# Game AI Patch 01 — Strategic Data Contract

Contract: `game.ai.patch-01.strategic-data-contract.v1`

Status: implemented data and validation foundation; no autonomous game behavior.

## Objective

Establish a versioned, inspectable contract for strategic actors, canonical facts, evidence, observations, private beliefs, memories, goals, candidate actions, checkpoints, and future decision receipts.

This patch deliberately stops before cognition, planning, action commitment, dialogue, campaign direction, or off-screen simulation.

## Added contract

All three default game projects now author the same definition at:

```text
project.metadata.strategicAI
```

The contract versions are:

```text
schema: game.strategicAI.v1
definitionVersion: game.strategicAI.definition.v1
stateVersion: game.strategicAI.state.v1
```

The machine-readable schema is:

```text
game_projects/schema/strategic-ai.v1.schema.json
```

The reusable cross-reference validator is:

```text
main_computer/strategic_ai_definition.py
```

## Constitutional separation

The contract keeps these records distinct:

```text
canonical fact
    what the settled world currently treats as authoritative

evidence
    an inspectable record that may be reliable, disputed, or unverified

observation
    what one actor directly perceived or received through a declared channel

belief
    what one actor currently treats as likely, with confidence and provenance

memory
    a retained episode or interpretation owned by one actor

goal
    a declared pressure that may later participate in action selection

decision receipt
    a future inspectable explanation of one selected strategic action
```

A false belief does not alter canonical truth. A disputed evidence record remains evidence rather than becoming a fact merely because an actor trusts it.

## Fixture boundary

The initial definition is intentionally mechanical. It does not pretend to be Vela Gate content.

It contains:

```text
1 strategic actor
2 canonical facts
1 disputed evidence record
1 direct observation
2 private beliefs
1 episodic memory
1 active goal
2 candidate action types
2 declared effect types
1 initial checkpoint
0 decision receipts
```

The Fixture Watch Officer is located at Haven orbit in Solace Reach. Direct telemetry supports the belief that a navigation relay is operational. A stale maintenance report supports a lower-confidence contradictory belief that the same relay is offline.

This proves that:

```text
canonical fact: relay operational = true
private belief: relay operational = false
```

may coexist without the belief mutating the fact.

## Validation responsibilities

JSON Schema owns:

- required fields;
- primitive types;
- closed objects;
- identifier shape;
- probability bounds;
- allowed actor, channel, memory, and visibility kinds;
- state and definition version constants;
- receipt and provenance field shape.

The Python validator owns:

- duplicate and globally reused identities;
- actor, goal, fact, evidence, source, effect, action, checkpoint, belief, memory, and receipt references;
- action-to-effect references;
- actor authority coverage for candidate actions;
- belief provenance;
- actor-state alignment;
- current-checkpoint validity;
- actor placement in an authored star system and local destination;
- canonical-fact independence from contradictory beliefs.

## Files changed

```text
game_projects/schema/strategic-ai.v1.schema.json
game_projects/new-game/project.json
game_projects/starter-game/project.json
game_projects/webgl-demo/project.json
main_computer/strategic_ai_definition.py
tests/test_strategic_ai_definition_contract.py
pretty_docs/game-ai-patch-01-strategic-data-contract.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Implemented

- Versioned strategic-AI definition and state contracts.
- Closed JSON Schema for strategic records.
- Identical deterministic fixtures in all three default projects.
- Stable strategic actor placement in the existing local-navigation contract.
- Canonical facts separated from actor beliefs.
- Evidence, observation, belief, and memory provenance.
- Candidate action types linked to declared effects and authorities.
- Empty but fully specified decision-receipt state.
- Reusable cross-reference validation.
- Focused regression tests.

## Not implemented

- Observation ingestion at runtime.
- Belief confidence updates.
- Memory retrieval.
- Utility scoring.
- Goal-oriented planning.
- Action proposals or atomic commitment.
- Generated decision receipts.
- Vela Gate actors.
- Solace Reach coordination.
- Captain modeling.
- Knowledge propagation.
- Campaign direction.
- Generated dialogue.
- Player-facing UI.

No player-visible sophistication should be claimed from this patch.

## Acceptance invariants

```text
all three default projects contain identical strategicAI definitions

every authored reference resolves

every actor resolves to one authored system and local destination

every belief has at least one provenance basis

false belief does not rewrite canonical fact

unknown effect ids fail validation

unknown actor, source, goal, checkpoint, or action ids fail validation

same project data produces the same initial strategic-AI state

no strategic action occurs because no cognition runtime exists
```

## Next patch

Patch AI-2 should add a deterministic headless cognition kernel that can:

```text
accept typed observations
→ update private belief confidence
→ retrieve bounded memories
→ score candidate actions
→ reject impossible candidates
→ choose deterministically
→ emit a decision receipt
→ serialize and restore state
```

It should not yet add open-ended dialogue or Vela Gate-specific behavior.
