# Game AI Patch 06 — Solace Reach Coordination and Promises

Contract: `game.ai.patch-06.solace-coordination.v1`

Status: implemented headless Solace Reach coordination prototype; no scene wiring, generated dialogue, campaign direction, or off-screen simulation.

## Objective

Prove that the existing strategic cognition, verified-action, social, and coordination layers can support finite rescue assets, typed promises, competing intervention order, and later cooperation changes without adding Solace-specific branches to the core runtimes.

```text
finite rescue asset
→ typed promise
→ competing verified plans
→ kept or broken resolution
→ explicit observations and trust update
→ later cooperation change
```

## Contract version

The schema family remains:

```text
game.strategicAI.v1
```

The closed contracts advance to:

```text
definitionVersion: game.strategicAI.definition.v5
stateVersion: game.strategicAI.state.v5
```

State v5 adds:

- typed commitment records;
- authored cooperation-model state;
- kept-or-broken commitment observations;
- commitment ancestry through the resolving action outcome;
- a deterministic `commitmentTrust` policy metric.

Migration from strategic-AI state v1 through v4 creates no historical promises. It initializes an empty commitment list and authored cooperation models at their declared initial trust.

## Runtime separation

The new generic runtime is:

```text
main_computer/web/applications/scripts/strategic-ai-commitment-runtime.js
```

Load order is:

```text
strategic-ai-runtime.js
→ strategic-ai-action-runtime.js
→ strategic-ai-social-runtime.js
→ strategic-ai-commitment-runtime.js
→ strategic-ai-coordinator.js
→ scene-viewer.js
```

The commitment runtime contains no Vela Gate or Solace Reach identifiers. Scenario data supplies the actors, resources, promise type, observations, trust profile, and policy weights.

## Typed commitments

A commitment type declares:

- allowed promisor actors;
- allowed promisee actors;
- required promise authority;
- pledged resource and amount;
- promised action type;
- observations emitted when kept;
- observations emitted when broken.

A runtime commitment records:

- commitment id and type;
- promisor and promisee;
- creation time and canonical revision;
- pending, kept, or broken status;
- resolving action outcome;
- resolution time and canonical revision;
- stable resolution reason;
- resulting observation ids.

Creating a commitment validates the parties, authority, promised action, and currently available resource. It does not reserve the resource. This is deliberate: another verified plan may consume the asset and expose the promise as broken.

## Commitment resolution

After every accepted action, the coordinator passes the outcome to the commitment runtime.

A pending promise is kept when:

```text
resolving actor = promisor
and
resolving action = promised action
```

A pending promise is broken when:

```text
accepted competing outcome
consumes the pledged resource amount
before the promised action commits
```

Implemented resolution reasons are:

```text
promised-action-committed
pledged-resource-diverted
```

Rejected actions do not resolve promises.

## Cooperation models

A cooperation profile declares:

- holder actor;
- subject actor;
- initial trust;
- kept-promise update strength;
- broken-promise update strength.

The update is bounded and deterministic:

```text
kept:
trust += (1 - trust) × keptDelta

broken:
trust *= (1 - brokenDelta)
```

The cognition runtime exposes the holder's average trust as:

```text
commitmentTrust
```

Action policies may weight that metric like every other authored strategic metric.

## Solace Reach actors

### Haven Relief Coordinator

Location:

```text
destination.solace-reach.haven-orbit
```

Responsibilities:

- coordinate simultaneous relief demands;
- promise the shared rescue shuttle;
- allocate that shuttle to Osprey when still available.

### Osprey Flotilla Captain

Location:

```text
destination.solace-reach.osprey-anchorage
```

Responsibilities:

- evacuate the civilian flotilla;
- protect sensitive civilian manifests;
- share those manifests after a kept promise;
- withhold them after a broken promise.

### Lyria Medical Coordinator

Location:

```text
destination.solace-reach.lyria-transfer
```

Responsibilities:

- protect critical patients;
- use emergency authority to claim the shared shuttle when acting first.

## Finite assets

The prototype adds:

```text
resource.solace.rescue-shuttle
capacity: 1
```

and a deferred resource for later coordination work:

```text
resource.solace.medical-pallet
capacity: 2
```

The rescue shuttle is consumed atomically by either:

```text
action.solace.allocate-shuttle-osprey
action.solace.claim-shuttle-lyria
```

Whichever verified action commits first exhausts the balance. The later action is rejected with:

```text
resource-unavailable
```

The rejected attempt leaves canonical facts, events, observations, resources, and revision unchanged.

## Promise fixture

The authored promise is:

```text
commitment.solace.shuttle-to-osprey
```

Parties:

```text
promisor: actor.solace.haven-coordinator
promisee: actor.solace.osprey-captain
```

Pledged resource:

```text
resource.solace.rescue-shuttle × 1
```

Promised action:

```text
action.solace.allocate-shuttle-osprey
```

Initial Osprey trust in Haven:

```text
0.55
```

## Kept ordering

```text
Haven promises shuttle to Osprey
→ Haven allocation commits first
→ shuttle balance becomes 0
→ commitment becomes kept
→ Osprey receives kept-promise observation
→ trust becomes 0.91
→ Osprey selects share civilian manifests
→ later Lyria claim is rejected
```

## Broken ordering

```text
Haven promises shuttle to Osprey
→ Lyria emergency claim commits first
→ shuttle balance becomes 0
→ commitment becomes broken
→ Osprey receives broken-promise observation
→ trust becomes 0.11
→ Osprey selects withhold civilian manifests
→ later Haven allocation is rejected
```

The two orderings begin from the same authored state and seed. Intervention order alone changes the commitment, beliefs, trust, and later verified choice.

## Canon and belief separation

Kept and broken promise observations update private beliefs. They do not create a canonical fact asserting that a promise is morally good or bad.

Canonical state records only the verified resource allocation and later manifest-sharing consequence. Trust remains actor-specific model state.

## Coordinator API addition

```javascript
const coordinator = MainComputerStrategicAICoordinator.create(definition, options);

const promise = coordinator.createCommitment(
  commitmentTypeId,
  promisorActorId,
  promiseeActorId,
  {createdAt}
);

const turn = coordinator.runTurn(actorId);
```

Turn results now include:

```text
commitmentMetrics
commitmentResolutions
```

Resulting commitment observations enter the same provenance-preserving belief-update path as verified action observations.

## Implemented proof

The focused tests prove:

- the commitment runtime loads before the coordinator;
- all five strategic scripts parse;
- the core commitment runtime contains no Vela or Solace branches;
- unauthorized actors cannot make the authored promise;
- a kept promise increases trust from 0.55 to 0.91;
- a broken promise decreases trust from 0.55 to 0.11;
- kept trust selects manifest sharing;
- broken trust selects manifest withholding;
- the first plan consumes the only rescue shuttle;
- the later competing plan is rejected without canonical mutation;
- commitment observations update beliefs without fabricating canonical truth;
- deterministic replay produces identical turns and states;
- save and restore preserve pending commitment behavior;
- v4 state migrates to empty commitments and the authored trust model;
- generated kept and broken states pass JSON Schema and cross-reference validation.

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
main_computer/web/applications/scripts/strategic-ai-social-runtime.js
main_computer/web/applications/scripts/strategic-ai-commitment-runtime.js
main_computer/web/applications/scripts/strategic-ai-coordinator.js
tests/test_strategic_ai_definition_contract.py
tests/test_strategic_ai_runtime.py
tests/test_strategic_ai_action_runtime.py
tests/test_strategic_ai_coordinator.py
tests/test_strategic_ai_vela_gate.py
tests/test_strategic_ai_social_runtime.py
tests/test_strategic_ai_solace_coordination.py
pretty_docs/game-ai-patch-06-solace-reach-coordination-and-promises.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Not implemented

- Scene or player-interaction integration.
- Generated promises or dialogue wording.
- Negotiation over alternative promise terms.
- Multiple-resource plan search.
- Promise deadlines independent of action outcomes.
- Faction-wide trust propagation.
- Campaign direction.
- Off-screen faction simulation.

## Next patch

The next bounded slice is AI-7, the authored campaign director:

```text
current strategic checkpoint
→ bounded authored opportunity set
→ one reversible intervention window
→ verified director receipt
→ no forced actor decision
→ unchosen routes remain viable
```

The director must consume the existing authority, revision, action, report, and commitment boundaries rather than bypass them.
