# Game AI Patch 07 — Bounded Campaign Director

Contract: `game.ai.patch-07.bounded-campaign-director.v1`

Status: implemented headless campaign-opportunity selection; no scene wiring, generated dialogue, off-screen scheduling, or forced actor decisions.

## Objective

Support just-in-time selection between major authored routes without allowing a campaign director to create evidence, issue actor actions, mutate canonical truth, or invalidate unchosen destinations.

```text
current strategic checkpoint
→ authored route opportunity
→ bounded activation receipt
→ actor-visible window observations
→ normal actor cognition and verification
→ reversible or expiring window
```

## Contract version

The schema family remains:

```text
game.strategicAI.v1
```

The closed contracts advance to:

```text
definitionVersion: game.strategicAI.definition.v6
stateVersion: game.strategicAI.state.v6
```

State v6 adds:

- authored campaign opportunity definitions;
- persisted campaign opportunity states;
- director activation, deactivation, and expiry receipts;
- fixed opportunity-window observations;
- conservative migration from state v1 through v5.

## Runtime separation

The new generic runtime is:

```text
main_computer/web/applications/scripts/strategic-ai-director-runtime.js
```

Load order is:

```text
strategic-ai-runtime.js
→ strategic-ai-action-runtime.js
→ strategic-ai-social-runtime.js
→ strategic-ai-commitment-runtime.js
→ strategic-ai-director-runtime.js
→ strategic-ai-coordinator.js
→ scene-viewer.js
```

The director runtime contains no Vela Gate or Solace Reach identifiers. Scenario data supplies route systems, checkpoints, observers, duration, source, channel, reliability, and visibility.

## Authored opportunity contract

Each campaign opportunity declares:

- opportunity id and label;
- route system;
- eligible strategic checkpoints;
- bounded window duration;
- observer actors;
- observation channel and system source;
- reliability and visibility;
- description.

It cannot declare:

- actor action types;
- effect types;
- evidence records;
- canonical fact changes;
- forced actor ids.

The closed JSON Schema rejects those fields. The Python validator also rejects them defensively.

## Fixed observation semantics

The director cannot author arbitrary propositions. Every activation observation is generated as:

```json
{
  "predicate": "predicate.campaign.opportunity-window-active",
  "arguments": [
    "<opportunity-id>",
    "<route-system-id>"
  ],
  "value": true
}
```

Deactivation and expiry emit the same proposition with:

```json
"value": false
```

This boundary prevents the director from inventing evidence or asserting scenario facts.

## Director receipts

Every transition creates a persisted receipt containing:

- receipt id;
- operation: `activate`, `deactivate`, or `expire`;
- opportunity and route system;
- strategic checkpoint;
- selection time;
- canonical revision binding;
- previous and next status;
- emitted observation ids;
- expiry time for activation;
- stable reason.

Allowed transitions are:

```text
available → active   activate
active → available   deactivate
active → closed      expire
```

No other transition is valid.

## Opportunity state

Each authored opportunity persists:

```text
status
activatedAt
expiresAt
activationReceiptId
activationCount
```

Initial opportunities are `available`.

Activating one route does not close, consume, or modify another route. The unchosen route remains available.

A selected route may be explicitly reversed before expiry. Reversal returns it to `available`.

When the authored deadline is reached, expiry closes only the selected opportunity and creates a separate receipt.

## Authored routes

### Vela Gate

```text
opportunity.campaign.vela-gate-intervention
routeSystemId: system.vela-gate
duration: 3
```

Observers:

- Gate Authority Official;
- Rescue-Guild Organizer;
- Beacon-Log Survivor.

### Solace Reach

```text
opportunity.campaign.solace-reach-intervention
routeSystemId: system.solace-reach
duration: 3
```

Observers:

- Haven Relief Coordinator;
- Osprey Flotilla Captain;
- Lyria Medical Coordinator.

Both are eligible at:

```text
checkpoint.strategic-ai.initial
```

## Coordinator API

```javascript
const coordinator = MainComputerStrategicAICoordinator.create(
  definition,
  options
);

const activation = coordinator.activateCampaignRoute(
  "system.vela-gate",
  {
    selectedAt: 1,
    canonicalRevision: 0,
    reason: "captain-selected-major-route"
  }
);
```

Reversal:

```javascript
const reversal = coordinator.deactivateCampaignOpportunity(
  "opportunity.campaign.vela-gate-intervention",
  {
    selectedAt: 2
  }
);
```

Expiry:

```javascript
const expired = coordinator.expireCampaignOpportunities(4);
```

The coordinator sends emitted observations through the existing provenance-preserving belief-update path. It does not call `decide`, create a proposal, or commit an actor action.

## Proven Vela selection

```text
select system.vela-gate at time 1
→ Vela opportunity becomes active
→ expiry time becomes 4
→ three Vela actors receive window-active observations
→ their private beliefs update
→ Solace opportunity remains available
→ canonical state is unchanged
→ actor receipts, proposals, and outcomes are unchanged
```

## Proven Solace selection

```text
select system.solace-reach at time 1
→ Solace opportunity becomes active
→ three Solace actors receive window-active observations
→ Vela opportunity remains available
→ no actor action is forced
```

## Proven reversal

```text
activate Vela
→ deactivate Vela before expiry
→ false window observations emitted
→ Vela returns to available
→ deactivation receipt persisted
```

A different major route can then be selected normally.

## Proven expiry

```text
activate Solace at time 3
→ expiry time becomes 6
→ advance director to time 6
→ Solace becomes closed
→ false window observations emitted
→ expiry receipt persisted
→ Vela remains available
```

## Hard boundaries

The director does not modify:

- canonical facts;
- canonical resources;
- canonical events;
- canonical revision;
- strategic decision receipts;
- action proposals;
- action outcomes;
- evidence definitions;
- commitments;
- cooperation models.

Actors continue to choose actions through authored policies. Consequences continue to require the existing action-authority verifier.

## Migration

State v1 through v5 migration:

- preserves all existing cognition, action, social, and commitment state;
- creates one `available` state for each authored opportunity;
- sets activation count to zero;
- creates no historical director receipt;
- invents no prior route selection.

All strategic runtimes perform the same conservative migration.

## Implemented proof

Focused tests prove:

- all six strategic runtimes parse;
- director load order precedes the coordinator;
- the director runtime contains no Vela or Solace branch;
- exactly two major authored opportunities exist;
- selecting either route activates the correct observer set;
- the unchosen route remains available;
- activation leaves canonical state, actor decisions, proposals, and outcomes unchanged;
- observations use only the fixed opportunity predicate;
- deactivation is reversible and receipted;
- expiry is bounded and receipted;
- unknown routes are rejected;
- duplicate activation is rejected;
- stale canonical revision binding is rejected;
- deterministic replay is stable;
- save and restore reproduce the same reversal;
- state-v5 migration creates available opportunity states and no receipts;
- closed schema rejects action or evidence controls;
- generated activation, reversal, and expiry states pass JSON Schema and cross-reference validation.

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
main_computer/web/applications/scripts/strategic-ai-director-runtime.js
main_computer/web/applications/scripts/strategic-ai-coordinator.js
tests/test_strategic_ai_runtime.py
tests/test_strategic_ai_action_runtime.py
tests/test_strategic_ai_coordinator.py
tests/test_strategic_ai_social_runtime.py
tests/test_strategic_ai_solace_coordination.py
tests/test_strategic_ai_campaign_director.py
pretty_docs/game-ai-patch-07-bounded-campaign-director.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Not implemented

- Player-facing route-selection UI.
- Scene activation or encounter spawning.
- Generated dialogue or communicative performance.
- Director scoring among more than one opportunity for the same route and checkpoint.
- Automatic clock integration outside explicit director calls.
- Evidence creation.
- Forced faction decisions.
- Off-screen faction simulation.

## Next patch

The next bounded slice is AI-8, communicative intent and generated performance:

```text
verified actor intent
→ permitted knowledge projection
→ authored speech-act structure
→ validated wording or template fallback
→ no state mutation from text
```

The game must remain fully playable with model access disabled.
