# Game AI Patch 03.1 — Strategic Turn Coordination and Revision Lock

Contract: `game.ai.patch-03-1.strategic-turn-coordination-and-revision-lock.v1`

Status: implemented headless turn coordination and stale-decision protection; no Vela Gate actors or player-facing behavior.

## Objective

Close the final core-runtime seam before authored multi-agent scenario work.

Patch AI-3 proved that typed proposals can be verified and committed atomically. Patch AI-3.1 adds three coupled guarantees:

```text
decision binds to exact canonical revision

actor scoring policy is authored in project data

one coordinator executes the complete deterministic turn
```

The patch remains mechanical and uses only the Fixture Watch Officer.

## Contract versions

The schema family remains:

```text
game.strategicAI.v1
```

The definition and state contracts advance to:

```text
definitionVersion: game.strategicAI.definition.v3
stateVersion: game.strategicAI.state.v3
```

The version advance is required because actor definitions, decision receipts, proposals, and migration behavior change shape.

## Canonical revision binding

Every new decision receipt records:

```text
canonicalRevision
```

Every proposal copies that same revision.

The action runtime validates:

```text
proposal revision
=
originating decision revision
=
current canonical revision
```

A proposal is rejected with:

```text
canonical-revision-stale
```

when another accepted action has advanced canonical state since the decision was made.

This closes the gap where two actors, or two decisions from one actor, could both reason from revision zero and submit after only one remained current.

## Honest legacy migration

State v1 and v2 are recognized by both strategic runtimes.

Historical v1 and v2 receipts did not record canonical revision or policy profile. Migration therefore does not invent those values.

Missing legacy bindings become:

```json
{
  "canonicalRevision": null,
  "policyProfileId": null
}
```

A legacy-unbound receipt remains inspectable but cannot create a new proposal. The action runtime reports that the decision has no canonical revision binding.

This is intentionally conservative. A fresh decision must be made against current state before a migrated game can commit another strategic action.

## Authored policy profiles

Strategic definitions now contain:

```text
policyProfiles
```

Each actor declares:

```text
policyProfileId
```

Each profile contains bounded, inspectable action policies:

```json
{
  "id": "policy.fixture.watch-officer",
  "actionPolicies": {
    "action.fixture.send-status-report": {
      "baseScore": 0,
      "weights": {
        "goalPriority": 0.5,
        "evidenceSupport": 1,
        "uncertainty": -0.6,
        "memoryRelevance": 0.1,
        "observationReliability": 0
      }
    }
  }
}
```

Supported metric names remain:

```text
goalPriority
evidenceSupport
uncertainty
memoryRelevance
observationReliability
```

The cross-reference validator requires every actor candidate action to have a policy in that actor's authored profile.

Runtime and per-decision policy overrides remain available for focused testing, but authored project data is now the default source of behavior.

## Decision receipts

New receipts record:

- actor;
- checkpoint;
- policy profile;
- canonical revision;
- active goals;
- private belief identities;
- candidate scores and score components;
- rejected candidates;
- selected action;
- expected effects;
- confidence;
- explicit seed.

This makes the scoring basis and world-state basis inspectable together.

## Strategic turn coordinator

The new module is:

```text
main_computer/web/applications/scripts/strategic-ai-coordinator.js
```

It loads after both lower-level runtimes and before `scene-viewer.js`.

The coordinator executes:

```text
ingest supplied observations
→ revise the acting actor's beliefs
→ evaluate authored policy
→ emit revision-bound decision receipt
→ register revision-bound proposal
→ verify and commit or reject
→ return committed observations to cognition
→ revise beliefs only for accepted resulting observations
→ return complete turn record and serializable state
```

The API is:

```javascript
const coordinator =
  MainComputerStrategicAICoordinator.create(definition, {
    state,
    seed
  });

const turn = coordinator.runTurn(actorId, {
  observations,
  checkpointId,
  decisionContext,
  parameters,
  proposalOptions
});

coordinator.snapshot();
```

The coordinator contains no scenario-specific branches.

## Complete turn record

A returned turn includes:

- turn id;
- actor;
- policy profile;
- checkpoint;
- canonical revision before and after;
- incoming observations;
- incoming belief updates;
- decision receipt;
- proposal;
- accepted or rejected outcome;
- resulting observations;
- resulting belief updates;
- complete final state.

The turn record is returned for inspection. Durable source-of-truth records remain in strategic state: receipts, proposals, outcomes, observations, beliefs, canonical events, fact values, resources, and revisions.

## Accepted observation loop

The action runtime appends resulting observations only after atomic commitment.

The coordinator then passes those already-committed observations through cognition:

```text
accepted action
→ resulting observation exists
→ actor belief update may occur
```

A rejected proposal produces:

```text
no resulting observation
no resulting belief update
no canonical revision change
```

## Fixture proof

Initial authored policy:

```text
strong operational telemetry
→ send status report
```

A new high-reliability contradictory observation produces:

```text
operational confidence falls
offline confidence rises
uncertainty rises
→ request relay inspection
```

Both choices are made without supplying a test-only action policy.

## Simultaneous stale-decision proof

```text
decision A at revision 0
decision B at revision 0

proposal A registered
proposal B registered

proposal A commits
→ revision 1

proposal B validates
→ canonical-revision-stale
→ no event, resource, fact, observation, or revision change
```

## Files changed

```text
game_projects/schema/strategic-ai.v1.schema.json
game_projects/new-game/project.json
game_projects/starter-game/project.json
game_projects/webgl-demo/project.json
main_computer/strategic_ai_definition.py
main_computer/web/applications.html
main_computer/web/applications/scripts/strategic-ai-runtime.js
main_computer/web/applications/scripts/strategic-ai-action-runtime.js
main_computer/web/applications/scripts/strategic-ai-coordinator.js
tests/test_strategic_ai_definition_contract.py
tests/test_strategic_ai_runtime.py
tests/test_strategic_ai_action_runtime.py
tests/test_strategic_ai_coordinator.py
pretty_docs/game-ai-patch-03-1-strategic-turn-coordination-and-revision-lock.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Acceptance invariants

```text
every new receipt records actor policy and canonical revision

every new proposal preserves its receipt revision

accepted proposal revision equals canonical revision before commit

stale proposal rejects before action-specific checks

stale rejection changes no canonical or cognitive result state

actor candidate actions are covered by authored project policy

same definition, state, observations, and seed reproduce the same complete turn

accepted resulting observations may revise beliefs

rejected actions produce no resulting belief update

v1 and v2 migration never fabricates a revision binding
```

## Not implemented

- Vela Gate actors.
- Faction knowledge propagation.
- Captain behavior modeling.
- Generated dialogue.
- Scene-viewer instantiation.
- Campaign direction.
- Off-screen simulation.
- Player-facing AI inspection tools.

## Next patch

The next bounded slice is the Vela Gate three-agent prototype.

It should author:

```text
Gate Authority official
rescue-guild organizer
survivor or evidence custodian
```

Each actor should use an authored policy profile and the existing coordinator. No Vela-specific branch should be added to the cognition, action, or coordination runtimes.
