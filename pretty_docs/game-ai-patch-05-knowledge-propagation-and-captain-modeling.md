# Game AI Patch 05 — Knowledge Propagation and Captain Modeling

Contract: `game.ai.patch-05.knowledge-propagation-and-captain-modeling.v1`

Status: implemented headless social-knowledge layer; no generated dialogue, scene wiring, campaign director, or off-screen faction scheduler.

## Objective

Allow strategic actors to learn only through observations they directly receive or reports transmitted over authored routes, while preserving source ancestry, confidence loss, bounded distortion, and actor-specific interpretations of the captain.

```text
direct observation
or explicit report
→ authorized route
→ recipient observation
→ private belief revision
→ actor-specific captain model
→ later deterministic action change
```

The social layer does not mutate canonical truth. Reports and captain models remain epistemic state.

## Contract version

The strategic-AI schema family remains:

```text
game.strategicAI.v1
```

The closed contracts advance to:

```text
definitionVersion: game.strategicAI.definition.v4
stateVersion: game.strategicAI.state.v4
```

Definition v4 adds:

- report routes;
- captain-model profiles;
- three captain tendency score metrics.

State v4 adds:

- report records;
- persisted captain models.

## Runtime

The new browser-safe module is:

```text
main_computer/web/applications/scripts/strategic-ai-social-runtime.js
```

Loader order is:

```text
strategic-ai-runtime.js
→ strategic-ai-action-runtime.js
→ strategic-ai-social-runtime.js
→ strategic-ai-coordinator.js
→ scene-viewer.js
```

The social runtime exposes:

```javascript
const social = MainComputerStrategicAISocialRuntime.create(definition, {
  state
});

social.createReport(
  routeId,
  senderActorId,
  recipientActorId,
  sourceObservationId,
  options
);

social.propagatePublicObservation(senderActorId, observationId);
social.updateCaptainModel(actorId, observationIds);
social.captainMetrics(actorId);
social.snapshot();
social.getReports();
social.getCaptainModels();
```

## Authored report routes

AI-5 adds three Vela Gate routes.

### Public network

```text
route.vela.public-network
```

Properties:

- all three Vela actors may send;
- all three may receive;
- only `public` observations may travel;
- no distortion is permitted;
- confidence is multiplied by the route reliability;
- the source observation remains explicit.

### Rescue-guild direct report

```text
route.vela.guild-direct
```

Properties:

- the organizer is the sender;
- the survivor is the recipient;
- private, restricted, or public observations may travel;
- no distortion is permitted;
- transmission is explicit rather than automatic.

### Rumor chain

```text
route.vela.rumor-chain
```

Properties:

- the survivor may send;
- the official or organizer may receive;
- distortion is allowed only up to the authored maximum;
- confidence is reduced by source reliability, route reliability, and distortion;
- the original observation and all parent reports remain recoverable.

## Report records

Every report records:

- report id;
- route;
- sender;
- recipient;
- source observation;
- original observation;
- parent report ids;
- transmitted proposition;
- reliability;
- distortion;
- send and receive times;
- delivered visibility;
- recipient observation.

A two-hop report therefore remains traceable:

```text
original organizer observation
→ direct guild report to survivor
→ rumor-chain report to official
→ recipient observation
```

The second report retains both the original observation id and its parent report id.

## Privacy boundary

A private observation cannot use a public route.

```text
private observation
+ public propagation request
=
explicit rejection
```

Private information moves only through an explicit route whose visibility policy permits it.

The coordinator automatically propagates only observations already marked `public`. It does not infer that a consequential or interesting act should become public.

## Public propagation

A public observation may fan out through an authored public route.

Each recipient receives a new observation with:

- the same proposition;
- the original source;
- a route-adjusted confidence;
- a report id;
- the original observation id;
- public visibility.

Public propagation still does not change canonical facts.

## Bounded rumor distortion

A rumor route may transmit a proposition different from its source only when:

- the caller declares a non-zero distortion;
- the route permits distortion;
- the declared distortion does not exceed the authored maximum.

The runtime computes:

```text
recipient reliability
=
source reliability
× route reliability
× (1 - distortion)
```

A distorted rumor may create or strengthen a false belief. It never changes canonical truth.

## Captain modeling

AI-5 models the captain as an observed subject, not a fully simulated strategic actor.

Each Vela actor has an authored captain-model profile containing:

- initial tendencies;
- recognized signal predicates;
- actor-specific tendency deltas.

The implemented tendencies are:

```text
tendency.captain.cooperation
tendency.captain.evidence-discipline
tendency.captain.authority-resistance
```

They become cognition metrics:

```text
captainCooperation
captainEvidenceDiscipline
captainAuthorityResistance
```

These values are probabilities in actor-private model state. Action-policy weights decide whether a tendency is desirable, threatening, or irrelevant.

## Different interpretations

The same observed act—

```text
captain retained independent evidence
```

—is interpreted differently.

### Gate Authority official

The official treats it mainly as authority resistance:

```text
authority resistance rises strongly
evidence discipline rises slightly
cooperation falls slightly
```

### Rescue-guild organizer

The organizer treats it mainly as disciplined independence:

```text
evidence discipline rises strongly
cooperation rises strongly
authority resistance rises slightly
```

### Survivor

The survivor treats it as cautious competence:

```text
evidence discipline rises
authority resistance rises moderately
cooperation rises cautiously
```

No universal morality or personality score is introduced.

## Coordinator integration

Before a decision, the coordinator now:

1. ingests direct observations;
2. delivers explicitly requested reports;
3. propagates explicitly identified public observations;
4. updates affected captain models;
5. revises recipient beliefs;
6. supplies the acting actor's captain metrics to cognition;
7. creates and verifies the action proposal.

After an accepted action, it:

1. identifies public resulting observations;
2. propagates them over public routes;
3. updates affected captain models;
4. revises all recipient beliefs;
5. persists one shared state.

Rejected actions still produce no resulting observation, report, captain-model update, or belief update.

## Demonstrated action change

Without captain evidence, the rescue organizer selects:

```text
restrict witness access
```

After a reliable public observation that the captain retained independent evidence, the organizer's evidence-discipline model rises above `0.7` and the authored policy selects:

```text
leak authenticated beacon evidence
```

The selected action still passes the AI-3 authority, location, precondition, resource, effect, and revision checks before canonical commitment.

## Persistence and migration

State v4 saves:

- reports;
- report ancestry;
- captain model tendencies;
- source observation ids;
- report ids used by each model;
- update times.

Cognition, action, and social runtimes migrate state v1, v2, or v3 to v4.

For a v3 state:

- `reports` becomes an empty list;
- captain models are initialized from authored profiles;
- no historical reports or captain observations are fabricated.

## Validation

The Python validator now checks:

- route actor and channel authorization;
- public-route privacy constraints;
- public-route no-distortion constraints;
- rumor-route distortion capability;
- captain profile actor uniqueness;
- tendency and signal consistency;
- report sender, recipient, source, route, latency, and distortion;
- report ancestry;
- recipient-observation back-links;
- captain-model profile, actor, tendency, observation, and report references;
- one persisted model per captain-model profile.

## Implemented proof

Focused tests prove:

- private observations cannot be broadcast publicly;
- explicit private reports deliver only to authorized recipients;
- public observations propagate to all authored recipients;
- confidence decays deterministically by route;
- two-hop reports preserve origin and parent ancestry;
- rumor distortion is bounded and leaves canonical truth unchanged;
- save and restore preserve report ancestry exactly;
- two actors interpret the same captain action differently;
- the organizer's captain model changes a later verified action;
- social replay is deterministic;
- state v3 migrates conservatively;
- generated social states remain schema-valid and cross-reference-valid.

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
main_computer/web/applications/scripts/strategic-ai-coordinator.js
tests/test_strategic_ai_definition_contract.py
tests/test_strategic_ai_runtime.py
tests/test_strategic_ai_action_runtime.py
tests/test_strategic_ai_coordinator.py
tests/test_strategic_ai_vela_gate.py
tests/test_strategic_ai_social_runtime.py
pretty_docs/game-ai-patch-05-knowledge-propagation-and-captain-modeling.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Not implemented

- Scene-viewer or player-interaction wiring.
- Generated dialogue or communicative performance.
- Full theory of mind.
- Universal captain morality or personality scoring.
- Reports traveling outside authored Vela routes.
- Solace Reach resource coordination.
- Campaign direction.
- Off-screen faction scheduling.

## Next patch

The next bounded slice is AI-6, the Solace Reach coordination prototype:

```text
finite rescue assets
+ competing actor plans
+ typed promises and commitments
+ verified resource consumption
+ cooperation changes after kept or broken promises
```

That patch should reuse the cognition, action, social, and coordinator runtimes without adding Solace-specific conditionals to the core.
