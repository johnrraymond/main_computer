# Game AI Patch 08 — Communicative Intent and Safe Wording

Contract: `game.ai.patch-08.communication.v1`

Status: implemented headless communicative-intent and knowledge-safe wording runtime; no scene performance, voice synthesis, free-form model dialogue, or player-facing conversation UI.

## Objective

Separate what an actor intends to communicate from how that intent is surfaced.

```text
authored communicative intent
→ speaker and audience authorization
→ permitted actor knowledge
→ optional constrained adapter selection
→ validated authored template
→ deterministic fallback wording
→ no strategic-state mutation
```

## Contract version

The schema family remains:

```text
game.strategicAI.v1
```

The closed definition and state contracts advance to:

```text
definitionVersion: game.strategicAI.definition.v7
stateVersion: game.strategicAI.state.v7
```

State v7 adds no communication transcript or generated-text state. Migration from v1 through v6 only advances the state version after the existing cognition, action, social, commitment, and director migration rules have supplied their required fields.

## Runtime

The new browser-safe runtime is:

```text
main_computer/web/applications/scripts/strategic-ai-communication-runtime.js
```

Load order is:

```text
strategic-ai-runtime.js
→ strategic-ai-action-runtime.js
→ strategic-ai-social-runtime.js
→ strategic-ai-commitment-runtime.js
→ strategic-ai-director-runtime.js
→ strategic-ai-communication-runtime.js
→ strategic-ai-coordinator.js
```

The communication runtime contains no Vela Gate or Solace Reach branches. Scenario definitions author the claims, intents, audiences, commitments, and templates.

## Authored communication claims

A communication claim contains:

- a structured proposition;
- authorized speakers;
- authorized audiences;
- a minimum belief confidence or observation reliability;
- one authored spoken phrase;
- descriptive authoring context.

The runtime accepts a claim only when the speaker currently owns a matching belief or observation at or above the declared threshold.

Canonical truth alone is not treated as actor knowledge.

## Communicative intents

An intent contains:

- a speech act;
- allowed speakers;
- allowed audiences;
- allowed communication claims;
- allowed structured commitment types;
- allowed speech templates;
- one deterministic fallback template.

Implemented speech-act values are:

```text
inform
request
promise
refuse
acknowledge
```

The patch authors three prototype intents:

```text
Vela survivor reports beacon sabotage
Vela official gives customs briefing
Haven coordinator confirms shuttle promise
```

## Safe surface templates

Templates are closed sequences of typed segments:

```text
literal
speaker-name
audience-names
claim
commitment-label
commitment-status
```

No template accepts arbitrary generated text.

Claim segments render only the authored `spokenText` of a validated claim. Commitment segments render only an existing structured commitment and its authored type label or current status.

## Constrained model adapter

A model adapter is optional. It may return only:

```json
{"templateId": "speech-template.example"}
```

The adapter receives only templates that already pass:

- intent membership;
- speaker authorization;
- audience authorization;
- knowledge threshold;
- secret-disclosure rules;
- structured-commitment requirements.

Adapter output is rejected when it:

- names an unsafe template;
- names a template outside the intent;
- includes raw text or any extra field;
- fails structurally;
- throws an error.

Every rejected or unavailable adapter path uses the authored fallback template.

Stable fallback reasons include:

```text
model-disabled
adapter-failed
adapter-output-invalid
adapter-template-unsafe
```

## Secret-knowledge proof

The Vela survivor holds a high-confidence private belief that the rescue organizer is compromised.

An intentionally unsafe authored template references that belief. Its claim authorizes no disclosure to the organizer.

When an adapter selects the unsafe template:

```text
adapter selection
→ audience validation fails
→ unsafe template excluded
→ deterministic safe fallback
→ secret phrase never appears
```

The accepted output contains only the separately authorized beacon-sabotage claim.

## Structured-promise proof

The Haven promise intent requires:

```text
commitment.solace.shuttle-to-osprey
```

The runtime refuses to perform the promise intent without a real commitment record.

After the existing commitment runtime creates a pending promise between Haven and Osprey, the communication runtime may render:

```text
Because the Osprey evacuation is critical, my commitment
Promise the rescue shuttle to Osprey is in force.
```

The wording references the exact commitment id. It does not create, modify, keep, break, or resolve the promise.

## State boundary

`performCommunication(...)` is pure with respect to strategic state.

It returns a deterministic communication result containing:

```text
communicationId
intentId
speechAct
speakerActorId
audienceActorIds
mode
fallbackReason
templateId
text
claimIds
knowledgeRecordIds
structuredCommitmentIds
canonicalRevision
```

It does not append observations, beliefs, receipts, proposals, outcomes, reports, commitments, director receipts, or canonical events.

The coordinator verifies that the communication runtime snapshot is byte-equivalent before returning the result.

## Coordinator API

```javascript
const result = coordinator.performCommunication(
  intentId,
  speakerActorId,
  audienceActorIds,
  {
    disableModel: true,
    commitmentId: null,
    modelAdapter: null
  }
);
```

## Implemented proof

Focused tests prove:

- all seven strategic scripts parse;
- the communication runtime loads before the coordinator;
- model-disabled operation always uses deterministic fallback;
- a valid adapter may choose a safe alternate template;
- the adapter receives only context-safe template ids;
- an unauthorized secret template falls back without disclosure;
- raw adapter text is rejected;
- adapter failure falls back;
- insufficient speaker knowledge rejects the communication;
- promise wording requires a structured commitment;
- promise wording links to the exact commitment id;
- communication leaves strategic and canonical state unchanged;
- deterministic replay produces identical communication ids and text;
- v6 state migrates to v7 without fabricated communication records;
- the definition remains JSON Schema and cross-reference valid.

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
main_computer/web/applications/scripts/strategic-ai-communication-runtime.js
main_computer/web/applications/scripts/strategic-ai-coordinator.js
tests/test_strategic_ai_runtime.py
tests/test_strategic_ai_action_runtime.py
tests/test_strategic_ai_coordinator.py
tests/test_strategic_ai_social_runtime.py
tests/test_strategic_ai_solace_coordination.py
tests/test_strategic_ai_campaign_director.py
tests/test_strategic_ai_communication_runtime.py
pretty_docs/game-ai-patch-08-communicative-intent-and-safe-wording.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Not implemented

- Free-form model prose.
- Natural-language semantic verification.
- Scene staging, gestures, timing, animation, or voice.
- Player dialogue choices.
- Transcript persistence.
- Generated negotiation terms.
- Communication-driven state mutation.
- Off-screen faction simulation.

## Next patch

The next bounded slice is AI-9:

```text
authored off-screen schedule
→ bounded deterministic update budget
→ verified strategic actions and reports
→ explicit deadlines for protected events
→ explainable state on return
```

Off-screen simulation must use the existing action, report, commitment, director, and communication boundaries rather than bypass them.
