# Game AI Patch 02 — Deterministic Cognition Kernel

Contract: `game.ai.patch-02.deterministic-cognition-kernel.v1`

Status: implemented headless cognition foundation; no canonical world effects or player-facing behavior.

## Objective

Add a deterministic strategic-AI runtime that can ingest typed observations, revise private beliefs, retrieve bounded memories, evaluate candidate actions, reject unavailable candidates, choose one action, emit an inspectable decision receipt, and serialize or restore private AI state.

This patch deliberately stops before action commitment, scenario integration, autonomous Vela Gate behavior, dialogue, campaign direction, or off-screen simulation.

## Runtime module

The browser-safe and Node-loadable runtime is:

```text
main_computer/web/applications/scripts/strategic-ai-runtime.js
```

It is included before `scene-viewer.js`, but the scene viewer does not instantiate it yet.

The exported API is:

```javascript
const ai = MainComputerStrategicAIRuntime.create(definition, {
  state,
  seed,
  actionPolicies
});

ai.ingestObservation(observation);
ai.updateBeliefs(actorId, observationIds);
ai.retrieveMemories(actorId, context, limit);
ai.evaluateCandidates(actorId, context);
ai.decide(actorId, checkpointId, context);
ai.snapshot();
ai.getReceipts();
ai.canonicalFacts();
```

## Determinism contract

```text
same definition
+ same starting state
+ same observations
+ same checkpoint
+ same seed
+ same action policies
=
same belief updates
+ same candidate scores
+ same selected action
+ same decision receipt
```

Tie ordering is derived from the explicit seed and stable record identities. No network, model service, wall-clock time, or ambient randomness participates.

## Observation ingestion

The runtime accepts only typed observations whose:

- id is unique;
- observer resolves to an authored strategic actor;
- channel resolves and is authorized for the actor;
- source resolves;
- proposition has an explicit predicate;
- reliability is bounded to `0..1`.

Observation ingestion changes only private strategic-AI state. It does not change canonical facts.

## Belief revision

Beliefs are matched by predicate and argument identity.

Supporting observations increase confidence toward one. Contradictory observations reduce confidence. Reliability controls update strength. Every applied observation id is appended to belief provenance.

If an actor receives a proposition for which it has no supporting belief, the runtime creates one deterministic private belief record and adds it to that actor's state.

The update rule is intentionally small and inspectable. It is not general natural-language inference.

## Memory retrieval

Memory retrieval is bounded and deterministically ranked from:

```text
salience
+ relevance to requested sources or proposition
+ recency relative to the selected checkpoint
```

Returned records include a transient `retrievalScore`. Retrieval does not rewrite memory or canon.

## Candidate metrics

The kernel computes five bounded actor metrics:

```text
goalPriority
evidenceSupport
uncertainty
memoryRelevance
observationReliability
```

Per-action scoring weights are supplied as bounded action policies when the runtime is created or when a decision is evaluated.

Action policies are not yet canonical project data. This prevents Patch AI-2 from pretending that a general strategic policy language is already settled. Vela Gate authoring and typed action consequences belong to later patches.

## Candidate rejection

Patch AI-2 rejects candidates when:

- the action type is unknown;
- the actor lacks a required authority;
- the caller marks the action unavailable.

The rejection is recorded with the candidate action id and reason.

This is not the full authority and consequence verifier. Patch AI-3 must still validate resources, locations, preconditions, exclusive spending, protected effects, and atomic world commitment.

## Decision receipts

Each decision receipt records:

- actor and checkpoint;
- active goals;
- private beliefs used by identity;
- all available candidate actions;
- score and score-component breakdown for every candidate;
- rejected candidates and reasons;
- selected action type;
- expected declared effects;
- decision confidence;
- explicit random seed.

The strategic-AI schema now allows optional numeric `scoreComponents` on each candidate action. Existing AI-1 definitions remain valid.

Decision receipts mutate only the strategic-AI private state. They do not assert that the selected action happened.

## Fixture proof

The existing Fixture Watch Officer provides the first deterministic proof.

Initial state:

```text
canonical fact:
    Haven navigation relay is operational

private beliefs:
    direct telemetry says operational at 0.96
    stale report says offline at 0.35
```

With the patch's test action policies:

```text
strong direct support
→ send status report selected
```

After a new high-reliability offline observation:

```text
operational confidence falls
offline confidence rises
uncertainty rises
→ request relay inspection selected
```

Canonical facts remain byte-equivalent before and after cognition.

## Files changed

```text
main_computer/web/applications/scripts/strategic-ai-runtime.js
main_computer/web/applications.html
game_projects/schema/strategic-ai.v1.schema.json
tests/test_strategic_ai_runtime.py
pretty_docs/game-ai-patch-02-deterministic-cognition-kernel.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Implemented

- Browser-safe and Node-loadable cognition runtime.
- Typed observation ingestion.
- Provenance-preserving belief revision.
- Deterministic creation of newly supported beliefs.
- Bounded memory retrieval.
- Actor cognition metrics.
- External bounded action-policy scoring.
- Authority and availability rejection.
- Seeded deterministic tie ordering.
- Decision receipt generation.
- State snapshot and restoration.
- Canonical-fact immutability checks.
- Schema-valid score-component receipts.
- Focused Node-backed regression tests.

## Not implemented

- World-state mutation from selected actions.
- Atomic effect commitment.
- Resource, location, or precondition verification.
- Pending proposal lifecycle.
- Scene-viewer instantiation.
- Vela Gate actors.
- Solace Reach coordination.
- Knowledge propagation between actors.
- Captain modeling.
- Generated dialogue.
- Campaign direction.
- Player-facing AI inspector.

No player-visible sophistication should be claimed from this patch.

## Acceptance invariants

```text
same inputs and seed reproduce the same receipt

new observation changes only private strategic-AI state

belief update preserves observation provenance

false belief never rewrites canonical fact

memory retrieval is bounded and deterministic

missing authority rejects a candidate

rejected candidates do not commit effects

decision receipt exposes every score component

save and restore reproduce the same next decision

runtime requires no network, credentials, or language model
```

## Next patch

Patch AI-3 should add a separate action authority and consequence kernel:

```text
selected strategic intention
→ typed action proposal
→ authority, resource, location, and precondition checks
→ accepted or rejected
→ atomic canonical effect commitment
→ resulting observations
→ finalized receipt
```

Only after that boundary is proven should the project replace the mechanical fixture with Vela Gate's first three strategic actors.
