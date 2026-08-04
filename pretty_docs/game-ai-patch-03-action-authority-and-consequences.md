# Game AI Patch 03 — Action Authority and Consequence Kernel

Contract: `game.ai.patch-03.action-authority-and-consequences.v1`

Status: implemented headless action-safety boundary; no Vela Gate actors or player-facing behavior.

## Objective

Turn a selected strategic intention into a typed action proposal that either commits all declared canonical effects atomically or changes no canonical world state.

The cognition runtime remains responsible for observations, private beliefs, memory retrieval, candidate scoring, and decision receipts. The new action runtime is responsible for authority, location, precondition, resource, effect, and commitment checks.

```text
decision receipt
→ typed proposal
→ authority and consequence verification
→ accepted atomic commit or explicit rejection
→ resulting typed observations
```

## Versioned contract

The strategic-AI schema family remains:

```text
game.strategicAI.v1
```

The closed definition and state contracts advance to:

```text
definitionVersion: game.strategicAI.definition.v2
stateVersion: game.strategicAI.state.v2
```

State v2 adds:

- mutable canonical fact values;
- resource balances;
- canonical events;
- registered action proposals;
- accepted or rejected outcomes;
- canonical revision numbers;
- resulting-observation ancestry.

Both strategic runtimes can migrate a v1 private-state payload into explicit v2 defaults. Migration creates no historical proposals, outcomes, events, or resource expenditure.

## Runtime separation

The cognition runtime remains:

```text
main_computer/web/applications/scripts/strategic-ai-runtime.js
```

The action runtime is:

```text
main_computer/web/applications/scripts/strategic-ai-action-runtime.js
```

The application loads them in this order:

```text
strategic-ai-runtime.js
→ strategic-ai-action-runtime.js
→ scene-viewer.js
```

Neither runtime is instantiated by `scene-viewer.js` in this patch.

## Action runtime API

```javascript
const actions = MainComputerStrategicAIActionRuntime.create(definition, {
  state
});

const proposal = actions.createProposal(decisionReceipt, parameters, options);
const validation = actions.validateProposal(proposal);
const outcome = actions.commitProposal(proposal);

actions.snapshot();
actions.getProposals();
actions.getOutcomes();
actions.getCanonicalState();
```

## Proposal lineage

A proposal records:

- proposal identity;
- originating decision receipt;
- actor;
- checkpoint;
- selected action type;
- local destination;
- bounded scalar parameters;
- exact requested effects;
- creation time.

The verifier proves that the stored proposal agrees with its originating receipt. A caller cannot use one actor's receipt, change the selected action, substitute another checkpoint, or request effects outside the selected action's allowlist.

## Validation order

The runtime rejects in a stable order:

```text
registered proposal identity
→ decision receipt
→ actor agreement
→ checkpoint agreement and freshness
→ selected action agreement
→ expected-effect agreement
→ action authority
→ actor and action location
→ canonical fact preconditions
→ resource availability
→ requested-effect allowlist
→ protected-effect authority
→ complete effect simulation
```

Each rejection has a stable code and explanation.

Implemented rejection families include:

```text
proposal-not-found
proposal-mismatch
receipt-not-found
actor-mismatch
checkpoint-mismatch
checkpoint-stale
action-mismatch
decision-effect-mismatch
missing-authority
wrong-location
unsupported-precondition
precondition-failed
resource-unavailable
effect-not-allowed
effect-not-found
protected-effect-forbidden
effect-commit-failed
```

## Atomic commitment

Accepted actions use copy-and-swap:

```text
clone canonical state
→ consume resources on clone
→ apply every effect on clone
→ build resulting observations separately
→ validate completion
→ advance revision once
→ replace active canonical state
```

When any operation fails, the candidate clone and candidate observations are discarded.

A rejected outcome:

- advances no canonical revision;
- commits no effect;
- consumes no resource;
- creates no resulting observation;
- preserves the previous canonical snapshot exactly.

Proposal and outcome records remain inspectable even when the world does not change.

## Canonical state

State v2 contains:

```text
canonicalState
├── revision
├── factStates
├── resourceBalances
└── events
```

Canonical fact state is separate from actor beliefs. An actor's belief update still cannot change world truth.

Resources have authored capacities and non-negative runtime balances. The cross-reference validator rejects missing, duplicate, over-capacity, or negative balances.

Canonical events retain:

- event id;
- committed effect type;
- actor;
- proposal;
- checkpoint;
- commit time;
- bounded scalar payload.

## Effects and protected consequences

Effect types now declare:

- category;
- operation;
- whether the effect is protected;
- additional authority required for protected use;
- optional fact target and value;
- description.

Implemented operations are:

```text
append-event
set-fact
```

Protected categories include:

```text
death
destroy-location
close-route-permanent
create-evidence
destroy-evidence
complete-scenario
transfer-faction
```

A protected effect must have an explicit authority gate. The fixture actor does not possess the authority required to disable the relay permanently.

## Resulting observations

Committed actions produce authored observation templates.

```text
canonical action commits
→ resulting observation is appended
→ cognition runtime may ingest it later
→ private beliefs may then change
```

The action runtime does not edit beliefs directly. World change and actor knowledge remain distinct.

## Mechanical fixture

The Watch Officer remains the only strategic actor.

### Send status report

Requirements:

- report authority;
- Haven orbit location;
- matching decision receipt.

Commit:

- append one status-report event;
- create one private action-result observation;
- consume no resource;
- preserve relay facts.

### Request relay inspection

Requirements:

- inspection-request authority;
- Haven orbit location;
- inspection-access fact equals true;
- one inspection-window resource remains.

Commit:

- consume exactly one inspection window;
- append one inspection-request event;
- create one private action-result observation.

A second request is rejected because the exclusive resource balance is zero.

### Protected relay disable

The fixture definition contains a protected `set-fact` effect and action for validation. It is not one of the Watch Officer's normal candidates. A test-only attempt is rejected because the actor lacks `authority.fixture.protected-relay-control`.

## Migration

Both runtimes recognize:

```text
game.strategicAI.state.v1
```

They migrate it to v2 by:

- preserving actor states, observations, beliefs, memories, and receipts;
- initializing canonical fact values from authored facts;
- initializing resource balances from authored capacities;
- setting revision to zero;
- creating empty proposals, outcomes, and events.

Migration never fabricates prior action history.

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
tests/test_strategic_ai_definition_contract.py
tests/test_strategic_ai_runtime.py
tests/test_strategic_ai_action_runtime.py
pretty_docs/game-ai-patch-03-action-authority-and-consequences.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Implemented proof

The focused suite proves:

- valid reports commit one event and observation;
- valid inspections consume one exclusive resource;
- the second inspection is rejected;
- wrong location rejects;
- false precondition rejects;
- stale checkpoint rejects;
- action substitution rejects;
- undeclared effect substitution rejects;
- protected effects require explicit higher authority;
- failure in a second effect rolls back the first effect and resource spend;
- rejected attempts create no observation;
- save and restore reproduce the same outcome and state;
- v1 private state migrates explicitly to v2;
- accepted and rejected states remain schema-valid and cross-reference-valid.

## Not implemented

- Scene-viewer instantiation.
- Vela Gate strategic actors.
- Faction knowledge propagation.
- Captain behavior modeling.
- Generated dialogue.
- Campaign direction.
- Off-screen faction simulation.
- Player-facing AI inspection tools.

No player-visible sophistication should be claimed from this patch.

## Next patch

The next bounded slice is the Vela Gate three-agent prototype:

```text
Gate Authority official
+ rescue-guild organizer
+ survivor or evidence custodian
→ distinct observations and private beliefs
→ deterministic intentions
→ verified proposals and consequences
→ inspectable surprising behavior
```

The first Vela Gate patch should use the existing cognition and action kernels rather than introduce system-specific reasoning branches.
