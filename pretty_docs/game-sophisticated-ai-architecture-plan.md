# Sophisticated Game AI Architecture and Implementation Plan

Contract: `game.sophisticated-ai-architecture-plan.v0.1`

Status: working design and implementation plan; no sophisticated-agent runtime is claimed as implemented.

## Purpose

The game should surprise players with the sophistication of its artificial intelligence.

The intended surprise is not that characters produce unlimited dialogue. It is that characters and factions appear to understand the world, maintain incomplete private knowledge, remember what the captain did, infer what the captain is likely to do, form plans, coordinate, deceive, revise their beliefs, and initiate consequential action.

The target player reaction is:

> I did not expect them to do that, but after I understood what they knew and wanted, their action made sense.

This document defines the architecture, authority boundaries, first two system applications, staged implementation program, and acceptance evidence required to pursue that result without allowing generated performance to silently invent the game.

## Relationship to existing game documents

This plan extends, but does not replace:

- `pretty_docs/game-star-system-density-and-choice-contract.md`
- `pretty_docs/game-forty-system-scenario-bible.md`
- `pretty_docs/game-scenarios-origin-region.md`
- `pretty_docs/game-warp-navigation-definition.md`
- `pretty_docs/game-warp-navigation-runtime.md`
- `pretty_docs/game-runtime-rearchitecture-plan.md`

The current repository already authors thirty-two flat warp destinations. Solace Reach and Vela Gate each have two stars, five worlds, five stable local destinations, and five connected local routes. The runtime can preserve current local-destination identity and choose an authored local arrival point after warp.

This document does not claim that player-controlled local transit, interacting multi-world scenarios, persistent faction cognition, adaptive dialogue, or off-screen political simulation already exist.

## Player-facing AI promise

The sophisticated AI should make the following experiences possible.

### Characters know different things

An official, a survivor, a refugee organizer, a patrol commander, and the captain should not share one omniscient world state.

Each may possess:

- direct observations;
- reports from trusted or distrusted sources;
- incomplete evidence;
- rumors;
- deliberate lies;
- outdated information;
- private commitments;
- uncertain interpretations.

### Characters form durable interpretations

A character may conclude that the captain is honest, dangerous, predictable, politically useful, easily diverted by civilian emergencies, or willing to preserve evidence for later leverage.

Those conclusions should be based on observed conduct and transmitted reports rather than a hidden universal morality score.

### Characters act before being prompted

Important actors may:

- contact the captain;
- move ships or supplies;
- alter patrols;
- protect or pressure witnesses;
- conceal evidence;
- form temporary alliances;
- leak information;
- prepare an ambush;
- offer concessions;
- change plans after learning the captain's destination.

They should not exist only as dialogue trees waiting for the player.

### Characters can be intelligently wrong

An actor may execute a coherent plan based on a false report, cultural assumption, stale memory, excessive confidence, or deception by another actor.

The game should distinguish:

```text
irrational random behavior
from
reasonable behavior under incorrect beliefs
```

### Consequences travel

A public act in one system may change what distant actors believe about the captain. The report may be accurate, distorted, classified, or deliberately manipulated.

Later actors should react to the information they plausibly received, not to inaccessible global truth.

### The game preserves authored significance

The AI may create variation in plans, timing, interpretation, and expression. It may not erase the campaign's authored people, history, evidence, locations, choices, or intended moral uncertainty.

## Core architectural decision

The game uses a hybrid AI architecture.

```text
canonical world simulation
        ↓
actor observations
        ↓
private beliefs and memories
        ↓
goals, obligations, fears, and relationships
        ↓
utility evaluation and planning
        ↓
proposed game action
        ↓
authority and consequence verification
        ↓
committed canonical action
        ↓
behavior execution
        ↓
communicative intent
        ↓
generated or authored performance
```

No single language model owns the world.

Classical state machines or behavior trees perform immediate physical behavior. Utility systems and planning choose among meaningful actions. Explicit social and belief models preserve continuity. A bounded campaign director shapes opportunity. Generative systems may interpret and express approved intentions, but they do not directly mutate canonical state.

## Constitutional constraints

These are the non-negotiable design rules.

### Canonical truth is structured

Canonical people, ships, factions, locations, resources, evidence, promises, injuries, deaths, route states, and political outcomes exist in inspectable game state.

Generated prose is not canonical merely because it was displayed.

### Observation is not truth

An actor's observation record says what the actor perceived or was told. It does not automatically prove the underlying proposition.

### Belief is not authority

An actor may believe, infer, suspect, or claim something without gaining permission to alter the world as though it were true.

### Description is not an action

A generated statement such as "the patrol has arrested the witness" cannot perform that arrest. The AI must propose an authorized game action whose preconditions, costs, participants, and effects are validated.

### Unknown remains unknown

Missing knowledge is not filled with model invention. The system may form a marked hypothesis with confidence and provenance.

### Unmentioned state is preserved

An accepted action changes only its declared effects. It does not casually rewrite unrelated relationships, resources, or history.

### Irreversible effects require explicit scope

Death, permanent route closure, destruction of a named settlement, or final resolution of a major crisis must pass stricter authority and timing rules than routine movement or communication.

### Important decisions are explainable

Every consequential AI decision produces an inspectable receipt containing active goals, supporting beliefs, considered alternatives, constraints, selected action, expected effects, and confidence.

### Performance cannot outrun state

Dialogue, voice, gesture, and presentation express an already selected communicative intent. They cannot introduce unverified canonical facts.

## Canonical world layer

The canonical layer contains facts the simulation treats as authoritative.

Examples include:

```text
actor identities
faction membership
physical location
ship and station condition
resource quantities
route availability
evidence records
promises and contracts
observed actions
public announcements
legal permissions
scenario state
accepted consequences
world time
```

Canonical facts should have stable identifiers and versioned persistence.

A useful fact record shape is:

```json
{
  "id": "fact.vela.manifest.ship-17",
  "predicate": "manifest_names_passenger",
  "arguments": ["record.manifest-17", "person.ines-varo"],
  "status": "confirmed",
  "sourceIds": ["evidence.manifest-17"],
  "createdAt": 18420,
  "visibility": "restricted"
}
```

The implementation does not need a universal logic engine in its first slice. It does need an explicit distinction between canonical fact records and actor beliefs about those records.

## Observation layer

Actors receive observations through declared channels:

- direct sight or sensors;
- communication;
- public broadcast;
- faction report;
- document or evidence inspection;
- inference from a visible consequence;
- rumor;
- campaign summary delivered across routes.

An observation should record:

```json
{
  "id": "observation.gate-official.0042",
  "observerId": "actor.gate-official",
  "content": {
    "predicate": "captain_retains",
    "arguments": ["evidence.manifest-17"]
  },
  "channel": "security_scan",
  "sourceId": "sensor.customs-array",
  "observedAt": 18510,
  "reliability": 0.86,
  "access": "private"
}
```

Observation channels matter because they determine what an actor could plausibly know and how strongly they should trust it.

## Belief model

Each strategic actor maintains a private belief state.

A belief needs:

```text
proposition
confidence
provenance
time acquired
last revision time
supporting observations
contradicting observations
visibility
whether the actor knows it is a lie
```

Example:

```json
{
  "id": "belief.gate-official.captain-will-publish",
  "holderId": "actor.gate-official",
  "proposition": {
    "predicate": "likely_action",
    "arguments": ["actor.captain", "action.publish_manifests"]
  },
  "confidence": 0.72,
  "basisIds": [
    "observation.gate-official.0042",
    "report.meridian.captain-exposed-contract-fraud"
  ],
  "updatedAt": 18525,
  "visibility": "private"
}
```

Beliefs may contradict canonical truth and one another. The runtime should not collapse all belief records into one faction-wide certainty unless an explicit communication or consensus process does so.

## Memory model

Memory should be divided by function rather than stored as one unbounded transcript.

### Episodic memory

Specific events involving time, location, participants, and perceived outcome.

### Semantic memory

Stabilized beliefs or knowledge such as faction rules, known relationships, or learned properties of a route.

### Commitment memory

Promises, threats, contracts, obligations, debts, and declared intentions.

### Social memory

How the actor believes another actor behaved, including witnesses and public visibility.

### Rumor memory

Claims whose provenance is indirect and whose wording may change in transmission.

### Reflective summaries

Bounded, derived interpretations produced from existing memories. A reflection must retain ancestry to the memories that support it.

Memory retrieval should use relevance, recency, importance, actor goals, and relationship context. Retrieval failure must be treated as a possible source of error rather than silently repaired with invention.

## Goals, values, obligations, and emotions

Important actors need more than one score.

The first model should distinguish:

- active goals;
- enduring values;
- legal or personal obligations;
- fears and risk tolerance;
- loyalties;
- relationships;
- emotional appraisals;
- resource constraints.

Example:

```json
{
  "actorId": "actor.rescue-organizer",
  "goals": [
    {
      "id": "goal.recover-missing-civilians",
      "priority": 0.94,
      "status": "active"
    },
    {
      "id": "goal.preserve-guild-network",
      "priority": 0.78,
      "status": "active"
    }
  ],
  "values": {
    "civilianLife": 0.95,
    "publicTruth": 0.81,
    "institutionalStability": 0.42
  },
  "obligations": [
    "promise.protect-witness-ines"
  ],
  "riskTolerance": 0.61
}
```

Emotion should alter attention, utility, confidence, and action thresholds. It should not act as a random behavior switch.

## Models of other minds

Strategic actors should maintain bounded models of important other actors.

The first useful model is not recursive unlimited theory of mind. It is:

```text
what I think they know
what I think they want
what I think they fear
what I think they will probably do next
how confident I am
```

Example:

```json
{
  "holderId": "actor.gate-official",
  "subjectId": "actor.captain",
  "estimatedGoals": [
    ["protect_civilians", 0.88],
    ["publish_truth", 0.67]
  ],
  "predictedActions": [
    ["inspect_missing_ship", 0.79],
    ["accept_quiet_repair_offer", 0.44]
  ],
  "basisIds": [
    "event.captain-rescued-convoy",
    "report.captain-kept-osprey-promise"
  ]
}
```

Different factions may interpret the same captain profile differently.

## Captain model

The game should infer demonstrated tendencies rather than ask the player to select a moral personality.

Candidate tendencies include:

```text
protects civilians before infrastructure
publishes evidence
preserves evidence for leverage
keeps explicit promises
avoids irreversible force
investigates before committing
accepts personal risk
defers to legal authority
distrusts official authority
responds to distress calls
protects crew over strangers
```

Each observation should indicate context. Rescuing civilians when there is no cost should count differently from rescuing them when doing so sacrifices a strategic opportunity.

The captain model is not universal truth. It is a set of interpretations held by the campaign system and by individual actors. Individual actors receive only the reports available to them and may misread the captain.

## Decision layer

The recommended decision layer combines three methods.

### Utility evaluation

Utility scoring compares candidate actions under competing pressures such as survival, legitimacy, loyalty, exposure, resource cost, promise keeping, civilian risk, and expected captain response.

Utility narrows the action set and handles trade-offs.

### Goal-oriented planning

Planning constructs a sequence of authorized actions whose preconditions and effects can achieve an active goal.

Planning is appropriate for:

- relocating evidence;
- forming a temporary coalition;
- repairing a corridor;
- isolating a witness;
- moving relief supplies;
- changing patrol coverage;
- negotiating access.

### Social practices

Authored social practices define recognizable multi-actor situations such as inspection, ceasefire negotiation, emergency triage, surrender, testimony, diplomatic reception, or public accusation.

A practice provides roles, expectations, legal actions, and likely interpretations without dictating one fixed outcome.

The combined decision process is:

```text
identify active goals
→ retrieve relevant beliefs and memories
→ generate candidate practices and plans
→ estimate utility and risk
→ reject unauthorized or impossible actions
→ select one proposal
→ verify effects
→ commit or explain failure
```

## Action proposal and verification

Agents do not directly execute arbitrary text.

They propose typed actions:

```json
{
  "actionType": "offer_exchange",
  "actorId": "actor.gate-official",
  "targetIds": ["actor.captain"],
  "parameters": {
    "offeredEffectId": "effect.restore-corridor-access",
    "requestedCommitmentId": "commitment.delay-publication"
  },
  "reasonIds": [
    "goal.preserve-gate-authority",
    "belief.captain-values-civilian-safety"
  ]
}
```

The verifier checks:

- referenced identities exist;
- the actor has authority;
- required resources exist;
- participants and targets are reachable;
- preconditions hold;
- timing is legal;
- effects are declared;
- irreversible scope is allowed;
- evidence access is plausible;
- the action does not invent protected facts.

Rejected proposals do not mutate the checkpoint. They produce a reason that can trigger replanning.

## Behavior execution

Accepted intentions are translated into reliable operational behavior.

Suitable execution mechanisms include:

- finite-state machines;
- behavior trees;
- state trees;
- navigation and steering;
- scripted animation;
- deterministic interaction handlers.

The strategic layer may decide to relocate a witness. The operational layer performs the actual sequence:

```text
assign escort
→ reach witness
→ confirm identity
→ move through legal route
→ react to interruption
→ arrive or fail
→ report outcome
```

Operational failure returns evidence to the strategic agent. It does not retroactively pretend the plan succeeded.

## Campaign director

The campaign director exists to support timing, pacing, and the promise that either major route can feel central.

It may:

- activate one of several authored crisis phases;
- choose which pressure becomes visible first;
- surface a distress call;
- advance or defer a reversible deadline;
- select an eligible encounter family;
- deliver a report from another system;
- bring already-existing actors into contact;
- preserve the viability of an unchosen destination.

It may not:

- make factions take actions contrary to their goals without an explicit cause;
- create evidence needed to force a preferred outcome;
- overwrite accepted consequences;
- declare every player action correct;
- destroy an unchosen system solely to validate the chosen route;
- invent a new canonical history through narration.

The director shapes opportunity. Actors retain their own beliefs, goals, and decisions.

## Generative performance boundary

Generative AI is optional at runtime and belongs after decision selection.

Its inputs should include:

```text
speaker identity
current beliefs
communicative intent
permitted facts
forbidden disclosures
relationship posture
emotional appraisal
conversation context
style constraints
```

Its output may provide:

- phrasing;
- conversational variation;
- summaries;
- voice performance;
- gesture suggestions;
- bounded clarification;
- interpretation of already-recorded events.

Its output must not directly provide:

- new canonical evidence;
- unregistered people or ships;
- undeclared resources;
- action effects;
- secret knowledge the speaker does not possess;
- promises the actor was not authorized to make;
- completion of objectives.

A generated utterance should be checked against a structured speech act:

```json
{
  "speechAct": "request_delay",
  "speakerId": "actor.gate-official",
  "targetId": "actor.captain",
  "allowedFactIds": [
    "fact.corridor-repair-time",
    "fact.customs-access-offer"
  ],
  "forbiddenFactIds": [
    "fact.hidden-salvage-owner"
  ],
  "requestedCommitmentId": "commitment.delay-publication"
}
```

When no generative model is available, the same intent should fall back to authored templates. The canonical simulation must remain playable.

## Decision receipts

Every consequential decision should produce a structured receipt.

Example:

```json
{
  "decisionId": "decision.gate-official.019",
  "actorId": "actor.gate-official",
  "checkpointId": "checkpoint.vela.18525",
  "activeGoalIds": [
    "goal.preserve-gate-authority"
  ],
  "beliefIds": [
    "belief.captain-will-publish",
    "belief.guild-has-partial-evidence"
  ],
  "candidateActionTypes": [
    "arrest_witness",
    "offer_exchange",
    "repair_corridor",
    "destroy_evidence"
  ],
  "rejections": [
    {
      "actionType": "arrest_witness",
      "reason": "witness_location_unknown"
    },
    {
      "actionType": "destroy_evidence",
      "reason": "evidence_not_under_actor_control"
    }
  ],
  "selectedActionType": "offer_exchange",
  "expectedEffects": [
    "delay_publication",
    "preserve_public_legitimacy"
  ],
  "confidence": 0.71,
  "randomSeed": 441920
}
```

Receipts serve four purposes:

1. designer debugging;
2. deterministic replay;
3. automated tests;
4. player-facing explanation fragments when the narrative calls for them.

The player does not need to see raw internal scoring.

## Simulation levels

The game should not run full cognition for every person in thirty-two systems.

### Strategic agents

Use full beliefs, goals, planning, relationships, and receipts for:

- major named characters;
- important ships;
- faction leadership;
- system governments;
- key institutions;
- communities treated as political actors.

### Operational agents

Use bounded local state and behavior execution for:

- patrols;
- bridge crew;
- delegates;
- rescue teams;
- boarding parties;
- active civilians;
- combat units.

Operational agents may inherit strategic intent without running full political planning.

### Statistical populations

Represent background populations through aggregate state:

- safety;
- health;
- food;
- migration;
- trust;
- labor;
- traffic;
- public opinion;
- casualty risk.

An aggregate may promote a named individual or group into an operational or strategic actor when the scenario requires it.

## Time and off-screen simulation

The simulation should use different update frequencies.

```text
visible local agents: continuous or event-driven
active system strategic agents: frequent decision windows
neighboring systems: coarse event windows
distant systems: pressure accumulation and scheduled events
```

Off-screen simulation may:

- move non-critical resources;
- change patrol posture;
- spread public reports;
- alter reversible relationships;
- advance repair, shortage, or political pressure;
- prepare plans.

Without an explicit communicated deadline or campaign rule, it should not silently:

- kill a major named character;
- destroy a major inhabited world;
- permanently close a campaign route;
- resolve a central scenario;
- remove a promised player opportunity.

This protects player agency while allowing the world to appear active.

## First AI vertical slice: Vela Gate

Vela Gate is the strongest first social-AI proof because its scenario already depends on evidence, authority, public legitimacy, concealment, and conflicting interpretations.

### Initial strategic actors

#### Gate Authority official

Public goal:

```text
restore safe navigation and maintain legal order
```

Private goals may include:

```text
preserve Gate Authority
contain evidence of predatory salvage
protect a superior or institution
avoid panic
```

#### Rescue-guild organizer

Goals may include:

```text
recover missing civilians
protect witnesses
expose deliberate beacon corruption
preserve the guild's access network
```

#### Survivor or evidence custodian

Goals may include:

```text
survive
protect another survivor
deliver evidence to a trusted recipient
avoid official custody
```

The captain is the fourth modeled actor, but the player retains direct control.

### Initial knowledge separation

The official knows:

- the public customs explanation;
- selected internal traffic records;
- some concealed operation details;
- what official sensors observed about the mother ship.

The organizer knows:

- patterns in disappearances;
- partial witness reports;
- which official claims conflict;
- uncertain information about the missing ship.

The survivor knows:

- direct events aboard one ship;
- the location or nature of one evidence record;
- only a fragment of the wider political structure.

No actor begins with the complete scenario truth.

### Required emergent behaviors

The first prototype should permit outcomes such as:

- the official offers immediate corridor repair in exchange for delayed publication;
- the organizer restricts sensitive information after interpreting the captain as too cooperative with customs;
- the survivor refuses rescue by an actor believed to be compromised;
- the official moves a patrol after inferring where the captain will investigate;
- the organizer leaks partial evidence to force public attention;
- an actor revises a plan after learning that the captain retained rather than surrendered evidence.

Each action must follow from authored goals, available beliefs, legal actions, and current resources.

### Minimum player-visible proof

A successful first slice should let a player notice that:

1. two actors know different things;
2. one actor acts before the player speaks to them;
3. one actor changes strategy after observing the captain;
4. one actor makes a coherent mistake;
5. an action in one local destination changes behavior at another;
6. a later explanation or evidence trail makes the surprising action understandable.

## Second AI vertical slice: Solace Reach

Solace Reach should prove that the architecture supports coordination and scarcity rather than only intrigue.

### Initial strategic actors

Possible actors include:

- Haven refuge authority;
- Osprey settlement coordinator;
- independent relief flotilla commander;
- displaced civilian coalition;
- damaged infrastructure controller.

### Required pressures

- limited transport capacity;
- competing distress calls;
- uncertain casualty estimates;
- promises to multiple populations;
- damaged local infrastructure;
- information arriving at different times;
- consequences of choosing which world receives aid first.

### Required contrast with Vela Gate

Vela Gate tests:

```text
evidence
deception
authority
prediction
public legitimacy
```

Solace Reach tests:

```text
coordination
scarcity
triage
trust
promise keeping
resource movement
```

If the same core AI model supports both systems without hardcoded system-specific decision logic, the architecture has passed its first generality test.

## Implementation patch series

The AI should be implemented through bounded replacement-file patches.

### AI-0 — Architecture and authoring contract

Objective:

- establish this design authority;
- define terminology, invariants, and proposed data shapes;
- add no runtime behavior.

Acceptance proof:

- documentation is indexed;
- claims clearly distinguish design from implementation.

Artifact boundary:

- documentation only.

### AI-1 — Canonical fact, observation, belief, and receipt schema

Objective:

- add versioned definitions for strategic actors, observations, beliefs, memories, goals, and decision receipts;
- add validators and deterministic fixture data.

Likely files:

- game definition schema;
- project definitions or separate scenario-definition files;
- validator modules;
- focused tests;
- implementation note.

Acceptance proof:

- duplicate ids and broken references fail;
- beliefs may contradict canonical truth without changing it;
- every belief has provenance;
- every receipt references an existing checkpoint and actor;
- all three game projects remain aligned.

Artifact boundary:

- data contract and validation only.

Implementation status:

- complete in `pretty_docs/game-ai-patch-01-strategic-data-contract.md`;
- the implemented fixture is mechanical and does not claim autonomous behavior.

### AI-2 — Deterministic cognition kernel

Objective:

- implement observation ingestion, belief update, memory retrieval, candidate generation, utility scoring, and receipt emission;
- no language model dependency.

Likely files:

- new AI runtime module;
- state defaults and save migration;
- focused fixtures and tests.

Acceptance proof:

```text
same initial checkpoint
+ same event sequence
+ same random seed
=
same belief updates
+ same selected action
+ same receipt
```

Artifact boundary:

- headless cognition kernel and tests; no player-facing dialogue.

Implementation status:

- complete in `pretty_docs/game-ai-patch-02-deterministic-cognition-kernel.md`;
- the runtime remains headless and selected actions do not mutate canonical world state.

### AI-3 — Action authority and consequence kernel

Objective:

- turn a selected intention into a typed proposal;
- validate receipt lineage, actor authority, location, canonical preconditions, resources, requested effects, and protected-effect authority;
- commit all effects atomically or preserve canonical state exactly.

Acceptance proof:

- a valid report commits one event and resulting observation;
- a valid inspection consumes one exclusive resource;
- a second inspection is rejected without changing canonical state;
- stale, wrong-location, precondition, substituted-action, undeclared-effect, and protected-effect proposals reject explicitly;
- failure in a later effect rolls back earlier effects and resource consumption;
- accepted and rejected states remain deterministic, persistent, schema-valid, and cross-reference-valid.

Artifact boundary:

- headless action proposal, verification, atomic commitment, migration, and tests;
- no scene integration or authored Vela Gate actors.

Implementation status:

- complete in `pretty_docs/game-ai-patch-03-action-authority-and-consequences.md`;
- definition and state contracts advance to v2 while retaining the `game.strategicAI.v1` schema family;
- selected actions now become world effects only through the separate authority-and-consequence runtime.

### AI-3.1 — Strategic turn coordination and revision lock

Objective:

- bind every new decision and proposal to the exact canonical revision used for reasoning;
- move default action scoring into authored actor policy profiles;
- add one headless coordinator for the complete observation-to-consequence turn.

Acceptance proof:

- a state change invalidates another decision made against the previous revision;
- stale rejection creates no canonical or cognitive result change;
- every actor candidate action is covered by its authored policy profile;
- the coordinator returns accepted observations to cognition but produces no belief update for rejected actions;
- identical state, observations, and seed reproduce the same complete turn;
- migrated v1 and v2 receipts remain explicitly unbound rather than receiving fabricated revision metadata.

Artifact boundary:

- revision locking, authored policy profiles, headless turn coordination, migration, and focused tests;
- no Vela Gate actors or scene integration.

Implementation status:

- complete in `pretty_docs/game-ai-patch-03-1-strategic-turn-coordination-and-revision-lock.md`;
- definition and state contracts advance to v3 while retaining the `game.strategicAI.v1` schema family;
- the core AI loop is now ready for authored multi-agent scenario content.

### AI-4 — Vela Gate three-agent prototype

Objective:

- author the official, organizer, and survivor;
- connect a small set of typed actions to existing Vela Gate scenario state.

Minimum actions:

- observe;
- report;
- conceal;
- request;
- offer exchange;
- relocate;
- leak;
- inspect;
- revise plan.

Acceptance proof:

- actors receive different observations;
- at least one initiates an action;
- at least one revises a plan;
- at least one acts coherently from a false belief;
- receipts explain every consequential choice;
- no action bypasses validation.

Artifact boundary:

- Vela Gate strategic AI only.

Implementation status:

- complete in `pretty_docs/game-ai-patch-04-vela-gate-three-agent-prototype.md`;
- three Vela Gate actors now have distinct observations, beliefs, memories, goals, locations, authorities, action vocabularies, and policy profiles;
- the headless prototype proves proactive action, cross-destination consequences, strategy revision, and one coherent false-belief action;
- no scene interaction, dialogue, generalized report propagation, or captain model is claimed.

### AI-5 — Knowledge propagation and captain modeling

Objective:

- transmit reports through actors and routes;
- infer context-sensitive captain tendencies;
- allow actors to react to what they plausibly heard.

Acceptance proof:

- private acts remain private unless witnessed or reported;
- public acts propagate with source and confidence;
- rumors may distort without becoming canonical truth;
- save and restore preserve report ancestry;
- two actors can interpret the same captain action differently.

Artifact boundary:

- social knowledge and captain-model layer.

Implementation status:

- complete in `pretty_docs/game-ai-patch-05-knowledge-propagation-and-captain-modeling.md`;
- definition and state contracts advance to v4 while retaining the `game.strategicAI.v1` schema family;
- three authored report routes now preserve privacy, source confidence, bounded distortion, and multi-hop ancestry;
- actor-specific captain models expose cooperation, evidence-discipline, and authority-resistance metrics to deterministic action policy scoring;
- no dialogue, scene wiring, universal morality score, or off-screen faction scheduler is claimed.

### AI-6 — Solace Reach coordination prototype

Objective:

- reuse the kernel for multi-party rescue, scarcity, and promises;
- prove resource and coordination behavior.

Acceptance proof:

- actor plans compete for finite ships or supplies;
- intervention order changes beliefs and commitments;
- a kept or broken promise changes later cooperation;
- no Vela-specific branches are added to the core kernel.

Artifact boundary:

- Solace Reach strategic AI and reusable resource-plan actions.

Implementation status:

- complete in `pretty_docs/game-ai-patch-06-solace-reach-coordination-and-promises.md`;
- definition and state contracts advance to v5 while retaining the `game.strategicAI.v1` schema family;
- three Solace Reach actors now compete over one shared rescue shuttle through verified actions and one typed non-reserving promise;
- kept and broken resolutions emit provenance-preserving observations, update actor-specific trust, and change Osprey's later verified cooperation;
- no Solace-specific branch is present in the generic commitment runtime.

### AI-7 — Bounded campaign director

Objective:

- support just-in-time major destination choice without overriding actor integrity.

Acceptance proof:

- selecting either major route activates a credible intervention window;
- director actions are restricted to authored opportunities;
- unchosen destinations remain viable;
- director cannot create evidence or force faction decisions;
- all interventions produce receipts.

Artifact boundary:

- campaign opportunity selection and tests.

Implementation status:

- complete in `pretty_docs/game-ai-patch-07-bounded-campaign-director.md`;
- definition and state contracts advance to v6 while retaining the `game.strategicAI.v1` schema family;
- two authored major-route opportunities activate bounded observer windows without issuing actor actions or mutating canonical truth;
- activation, reversal, and expiry each persist a director receipt and emit only the fixed opportunity-window proposition;
- unchosen routes remain available, while selected windows are reversible before their authored deadline.

### AI-8 — Communicative intent and generated performance

Objective:

- separate speech acts from wording;
- add authored-template fallback;
- optionally integrate a model adapter.

Acceptance proof:

- the game remains playable with model access disabled;
- generated text can reference only permitted facts;
- generated statements cannot mutate state;
- secret knowledge is not disclosed without authorization;
- accepted commitments exist as structured state;
- model failure falls back without losing the scenario.

Artifact boundary:

- dialogue intent, validation, adapter, fallback templates, and tests.

Implementation status:

- complete in `pretty_docs/game-ai-patch-08-communicative-intent-and-safe-wording.md`;
- definition and state contracts advance to v7 while retaining the `game.strategicAI.v1` schema family;
- authored claims, intents, and typed speech templates separate actor intent from surface wording;
- the optional adapter may select only prevalidated authored templates and can never inject raw text;
- knowledge thresholds, explicit audiences, and structured commitment bindings prevent secret leakage and text-created promises;
- model-disabled, invalid-output, unsafe-template, and adapter-failure paths use deterministic fallback without strategic-state mutation.

### AI-9 — Off-screen faction simulation

Objective:

- add coarse strategic updates outside the active system.

Acceptance proof:

- distant updates are bounded and reproducible;
- irreversible protected events require explicit deadlines or authority;
- reports travel at authored speeds;
- returning to a system reveals explainable changes;
- simulation cost remains within a declared budget.

Artifact boundary:

- strategic scheduler and persistence.

Implementation status:

- complete in `pretty_docs/game-ai-patch-09-deterministic-budgeted-offscreen-simulation.md`;
- definition and state contracts advance to v8 while retaining the `game.strategicAI.v1` schema family;
- two authored schedules exercise director, action, report, commitment, communication, timing, and budget boundaries;
- the active system is excluded, report delivery honors route latency, and every run persists an explainable receipt;
- protected irreversible steps require both authored authority and an explicit deadline before the schedule is accepted.

### AI-9.1 — Live strategic session and developer harness

Objective:

- own one persistent strategic session for the loaded Game Surface project;
- make the completed headless stack directly observable and operable in the browser.

Acceptance proof:

- the same project render reuses one strategic session instead of resetting state;
- active-system navigation updates persist without duplicating strategic operations;
- developer controls invoke real actor turns, director transitions, commitments, communication, and off-screen simulation;
- snapshot export, reset, import, and local reload preserve verified state;
- incompatible project or definition snapshots are rejected.

Artifact boundary:

- browser session owner, Game Surface binding, developer panel, and live-session tests.

Implementation status:

- complete in `pretty_docs/game-ai-patch-09-1-live-strategic-session-and-developer-harness.md`;
- one project-scoped session now owns the existing v8 state without changing the strategic schema or authored scenarios;
- Game Surface project loading and navigation bind to the session while scene rerenders and app switching preserve state;
- a browser panel exposes verified actor turns, bounded off-screen advancement, campaign transitions, typed commitments, knowledge-safe communication, and snapshot controls;
- player-facing Vela interactions and automatic travel-triggered off-screen advancement remain separate slices.

### AI-9.2 — Player-visible Vela Gate interaction

Objective:

- expose one normal Vela Gate interaction through the live strategic session;
- present one verified actor choice, safe briefing, and readable decision explanation.

Acceptance proof:

- the interaction appears only while Vela Gate is the active system;
- requesting a briefing activates the authored Vela opportunity and runs the real Gate Authority turn;
- the selected action commits through the existing verifier and the briefing passes the communication safety boundary;
- confidence, major score signals, alternatives, observations, resources, and canonical revision are player-visible;
- reopening or rerendering does not repeat the turn or mutate the completed session.

Artifact boundary:

- Game Surface Vela channel card, Vela presentation controller, focused live-interaction tests, and documentation.

Implementation status:

- complete in `pretty_docs/game-ai-patch-09-2-player-visible-vela-gate-interaction.md`;
- the Vela Gate Authority card is hidden outside `system.vela-gate` and uses the AI-9.1 project session rather than a second state store;
- the baseline interaction activates the authored opportunity, verifies `action.vela.move-patrol-to-chiron`, renders the authored official briefing, and explains the decision;
- completion is reconstructed from persisted strategic receipts and outcomes, so reopening the interaction does not create another actor turn;
- automatic travel-triggered off-screen advancement and player-facing return summaries are completed in AI-9.3.

### AI-9.2.1 — Live interface polish

Objective:

- make the first player-facing Vela card readable at constrained Game Surface sizes;
- separate developer controls from the player interaction;
- translate internal decision metrics into concise player language without changing AI behavior.

Acceptance proof:

- the card responds to its own width and stacks explanation sections when narrow;
- verified consequences render as labeled rows;
- raw score-component identifiers and decimal scores are absent from the player presentation;
- the completed state remains legible and clearly non-actionable;
- the same verified action, safe briefing, revision, observations, resource use, and idempotence tests still pass.

Artifact boundary:

- Vela interaction markup, presentation controller, Vela and debug styles, focused tests, and documentation.

Implementation status:

- complete in `pretty_docs/game-ai-patch-09-2-1-live-interface-polish.md`;
- the developer toggle is positioned away from the lower-right player card;
- the explanation uses two columns when wide, one column when narrow, and a full-width verified-outcome section;
- player wording now uses authored labels such as `Mission priorities`, `Available evidence`, and `Viable alternative`;
- strategic definitions, policies, state, and verified outcomes are unchanged.

### AI-9.3 — Travel-triggered off-screen progression and return summary

Objective:

- advance the existing off-screen scheduler exactly once after a committed navigation arrival;
- exclude the newly active system;
- present persisted authored developments when the player returns.

Acceptance proof:

- arrival identity is derived from completed route, arrival timestamp, destination, and world time;
- repeated callbacks, rerenders, reloads, and compatible snapshot restores do not repeat an arrival;
- navigation world time advances the scheduler only after travel is committed;
- the existing four-unit budget and deterministic step order remain intact;
- the destination schedule is skipped;
- a returning system receives a player-facing diff derived from stored step receipts and authored descriptions;
- acknowledgement persists without changing strategic state.

Artifact boundary:

- strategic-session travel metadata, navigation integration controller, Game Surface return panel, focused tests, and documentation.

Implementation status:

- complete in `pretty_docs/game-ai-patch-09-3-travel-triggered-offscreen-progression-and-return-summary.md`;
- Solace-to-Vela travel processes the three Solace relief steps while excluding Vela;
- Vela-to-Solace travel processes three Vela steps and defers the briefing at the four-unit budget;
- the first Solace return shows three receipt-derived developments;
- processed arrival keys, absence baselines, return notice, and acknowledgement survive compatible snapshot restore;
- no timers, wall-clock polling, strategic schema changes, or project-definition changes were added.

### AI-9.4 — Player-visible Solace Reach coordination

Objective:

- expose the typed shuttle promise and one-shuttle resource conflict through normal Game Surface interaction;
- prove that kept or broken commitments change later Osprey cooperation through the existing trust model.

Acceptance proof:

- the card appears only in `system.solace-reach`;
- the opening step creates one typed commitment and renders wording bound to that commitment;
- the player selects which already-authored actor receives the scarce-resource turn;
- both allocations pass through the normal action, authority, precondition, revision, and resource verifier;
- the promise resolves as kept or broken from the committed action outcome;
- trust becomes the authored `0.91` or `0.11`;
- the Osprey captain independently selects manifest sharing or withholding from `commitmentTrust`;
- both branches survive rerender, replay, and compatible snapshot restore without duplicate turns.

Artifact boundary:

- Solace Game Surface card, Solace presentation controller, live interaction tests, and documentation.

Implementation status:

- complete in `pretty_docs/game-ai-patch-09-4-player-visible-solace-coordination.md`;
- the initial card shows one available rescue shuttle and Osprey trust `0.55`;
- the kept branch allocates the shuttle to Osprey, resolves the promise kept, raises trust to `0.91`, and produces manifest sharing;
- the broken branch lets Lyria exercise emergency authority, resolves the promise broken, reduces trust to `0.11`, and produces manifest withholding;
- no strategic schema, project data, policy, verifier, travel, or return-summary changes were required.

### AI-9.4.1 — Non-obscuring collapsible strategic UI

Objective:

- prevent Vela, Solace, and return-summary cards from covering the playable scene;
- provide persistent full, compact, and collapsed presentation states.

Acceptance proof:

- wide surfaces reserve a separate right-hand dock track;
- narrow surfaces reserve a separate bottom-drawer track;
- the playfield and dock bounding boxes do not overlap;
- collapsing restores almost the full playfield while retaining an obvious expand tab;
- panel modes persist independently across rerenders and reloads;
- existing Vela, Solace, travel, and strategic behavior tests remain unchanged.

Artifact boundary:

- shared dock markup, layout CSS and controller, focused panel-layout tests, and documentation.

Implementation status:

- complete in `pretty_docs/game-ai-patch-09-4-1-non-obscuring-collapsible-strategic-ui.md`;
- all three player-facing strategic cards are direct children of one dock;
- `ResizeObserver` selects side or bottom layout from the real host width and preserves minimum playfield height;
- the dock supports expanded, compact, and collapsed modes without timers or strategic-state changes;
- desktop and narrow headless-browser geometry checks prove non-overlap.

### AI-10 — Tactical and learned behavior experiments

Objective:

- investigate learning for ship maneuvering, crowd evacuation, combat coordination, or automated playtesting;
- do not use learned policies as canonical political authority.

Acceptance proof:

- trained policy operates behind a stable action interface;
- deterministic fallback exists where gameplay requires it;
- policy cannot bypass damage, authority, or resource rules;
- performance and failure bounds are measured.

Artifact boundary:

- optional experimental subsystem, separate from social-cognition authority.

## Save-state requirements

Sophisticated AI requires persistent state for:

- strategic actor identity;
- goals and active plans;
- beliefs and confidence;
- memory records;
- promises and obligations;
- relationships;
- captain-model interpretations;
- pending proposals;
- accepted action receipts;
- public and private reports;
- director opportunities;
- random seeds or deterministic choice records.

Save migration must preserve old games that do not contain AI state by creating explicit defaults rather than fabricating past memories.

## Evaluation program

The AI should be evaluated on behavior, not only dialogue quality.

### Coherence

Can a decision be traced to goals, beliefs, constraints, and available actions?

### Knowledge discipline

Does an actor avoid using information it could not possess?

### Initiative

Do actors begin meaningful actions without waiting for the player?

### Adaptation

Do actors revise beliefs and plans after new evidence?

### Continuity

Do later actions reflect earlier promises, reports, and consequences?

### Legibility

Can a player or designer understand the action after receiving enough evidence?

### Surprise

Does the action avoid being obvious while remaining coherent in retrospect?

### Diversity

Can the same authored scenario produce materially different but valid plans under different observations and captain behavior?

### Robustness

Does save/load, delayed arrival, unexpected player order, missing model access, or rejected action handling preserve coherent state?

### Authorial control

Can designers constrain protected facts, required beats, forbidden outcomes, and irreversible effects without scripting every line?

## Test matrix

The first automated suite should include:

| Test | Required proof |
| --- | --- |
| No omniscience | actor cannot react to unobserved evidence |
| False belief | coherent plan may follow from incorrect information |
| Belief revision | stronger contradictory evidence changes confidence |
| Provenance | every belief and report retains source ancestry |
| Authority rejection | unauthorized action leaves checkpoint unchanged |
| Resource conservation | two plans cannot spend the same exclusive resource |
| Deterministic replay | same inputs and seed reproduce the receipt |
| Save restoration | beliefs, plans, and obligations restore exactly |
| Secret protection | generated speech cannot expose forbidden fact ids |
| Template fallback | scenario remains playable without a model |
| Director restraint | director cannot create canonical evidence |
| Unchosen viability | deferred destination retains a valid intervention path |
| Cross-system report | later actor reacts only after plausible delivery |
| Captain-model context | costly and costless rescues are not treated identically |
| Explanation | consequential action has a complete receipt |

## Failure modes to avoid

### Fluent but empty characters

The actor speaks convincingly but has no durable goals, memory, or effect on the world.

### Hallucinated canon

Dialogue invents a ship, witness, law, relationship, or historical event that the simulation never authored.

### Omniscient factions

Every actor instantly knows what the player did everywhere.

### Reputation-number substitution

A single positive or negative score replaces beliefs, obligations, fear, trust, public legitimacy, and conflicting interpretations.

### Random betrayal

A character changes sides for surprise without a traceable reason.

### Director puppetry

The campaign director forces actors to violate their goals to preserve a desired story beat.

### Model-dependent gameplay

The scenario cannot proceed when a remote model is unavailable or changes behavior.

### Unbounded off-screen loss

The player loses major people, worlds, or opportunities without warning or a recoverable rule.

### Hidden non-determinism

Designers cannot reproduce why an actor made a consequential choice.

### Full-fidelity simulation everywhere

The game spends excessive compute pretending every civilian is a strategic agent.

## Current implementation boundary

As of the source snapshot for this document:

Implemented:

- thirty-two flat warp systems;
- forty-two inter-system routes;
- two rich binary systems;
- ten authored local destinations;
- ten authored local routes;
- local-destination identity and discovery state;
- save restoration for current local destination;
- authored local arrival selection after warp;
- route-choice Captain's Scrawl presentation;
- scenario and regional design documents;
- versioned strategic-AI definition and state contracts;
- machine-readable strategic-AI schema;
- cross-reference validator for actors, facts, evidence, beliefs, actions, checkpoints, receipts, and local placement;
- identical deterministic strategic-AI fixtures in all three default projects;
- one mechanical actor with canonical facts, a sourced false belief, memory, goal, and candidate actions;
- browser-safe deterministic strategic cognition runtime;
- typed observation ingestion and provenance-preserving belief revision;
- bounded deterministic memory retrieval;
- actor cognition metrics and deterministic utility scoring;
- authored actor policy profiles with complete candidate-action coverage;
- authority and availability rejection;
- deterministic decision receipts bound to policy profile and canonical revision;
- strategic-AI state snapshot and restoration;
- strategic-AI definition and state contracts v8;
- separate browser-safe action authority and consequence runtime;
- typed proposal registration tied to stored decision receipts and canonical revision;
- stale-revision, action authority, local-destination, canonical precondition, resource, effect-allowlist, and protected-effect checks;
- copy-and-swap atomic canonical effect commitment;
- canonical revision, fact-state, resource-balance, event, proposal, and outcome persistence;
- typed resulting observations that do not directly alter private beliefs;
- browser-safe strategic turn coordinator for observation ingestion, cognition, proposal, commitment, and resulting belief revision;
- conservative v1 and v2 migration that leaves historical revision and policy bindings explicitly unbound;
- three authored Vela Gate strategic actors at Velaris Gate Central, Seraph Relay, and Chiron Observatory;
- separate Vela observations, beliefs, memories, goals, authorities, actions, resources, and policy profiles;
- ten verified Vela action types covering patrol movement, repair offers, record control, inspection, evidence disclosure, witness protection, rescue requests, evidence custody, refusal, and signaling;
- cross-actor resulting-observation delivery and belief revision;
- deterministic proof of proactive action, a reliable-observation strategy change, cross-destination behavioral influence, and a coherent false-belief decision;
- separate browser-safe social-knowledge runtime;
- three authored Vela report routes for public, direct private, and bounded-rumor transmission;
- provenance-preserving report records with origin observations, parent reports, recipient observations, confidence decay, distortion, and route latency;
- persisted actor-specific captain models with cooperation, evidence-discipline, and authority-resistance tendencies;
- deterministic coordinator integration for report delivery, public propagation, captain-model updates, and later verified action changes;
- conservative v1, v2, and v3 migration into v4 social state;
- separate browser-safe typed commitment and cooperation runtime;
- authored promise types with allowed parties, authority, pledged resources, promised actions, and kept-or-broken observation templates;
- persisted commitment records linked to canonical revisions and resolving action outcomes;
- persisted actor-specific cooperation models and deterministic `commitmentTrust` policy scoring;
- three authored Solace Reach actors at Haven orbit, Osprey anchorage, and Lyria transfer orbit;
- one shared rescue shuttle consumed atomically by competing Osprey and Lyria plans;
- deterministic proof that intervention order keeps or breaks a promise and changes later manifest-sharing cooperation;
- conservative v1 through v4 migration into v5 commitment state;
- separate browser-safe bounded campaign-director runtime;
- two authored major-route opportunities for Vela Gate and Solace Reach;
- persisted opportunity states and activation, deactivation, and expiry receipts;
- fixed director observations that cannot create evidence or name actor actions;
- deterministic proof that either route activates a credible observer window while the unchosen route remains viable;
- reversible route selection before expiry and bounded closure at the authored deadline;
- conservative v1 through v5 migration into v6 director state;
- separate browser-safe communicative-intent and knowledge-safe wording runtime;
- four authored communication claims, three communicative intents, and seven typed speech templates;
- claim validation against actor-owned beliefs or observations, minimum confidence, and explicit authorized audiences;
- optional constrained adapter selection that accepts only a validated template id and rejects raw generated text;
- deterministic model-disabled, invalid-output, unsafe-template, and adapter-failure fallback;
- structured promise wording bound to an existing commitment id without creating or resolving commitments;
- pure communication results that leave canonical and strategic state unchanged;
- conservative v1 through v6 migration into v7 communication-compatible state;
- separate browser-safe deterministic off-screen scheduler;
- two authored Vela Gate and Solace Reach off-screen schedules with typed step costs and due times;
- active-system exclusion, strict four-unit per-run budget, deterministic ordering, and budget deferral;
- report readiness derived from authored route latency;
- protected-step validation requiring explicit deadlines and complete action/effect authority;
- persisted off-screen step state, simulation receipts, and explainable return summaries;
- conservative v1 through v7 migration into v8 scheduler state;
- project-scoped browser strategic-session owner with deterministic definition fingerprinting;
- local session persistence, compatible snapshot import/export, reset, and explicit mismatch rejection;
- Game Surface project and navigation binding that preserves strategic state across rerenders and app switching;
- developer inspection panel for real actor turns, off-screen advancement, campaign opportunities, commitments, communication, and full state inspection;
- player-visible Vela Gate Authority channel using the live session, verified official action, safe briefing, and readable decision explanation;
- exact-once navigation arrival integration with bounded off-screen progression and player-facing receipt-derived return summaries;
- player-visible Solace Reach coordination with typed promise wording, one-shuttle allocation, kept-or-broken resolution, and trust-driven Osprey response.

Not implemented:

- general goal-oriented planning beyond bounded candidate evaluation;
- full theory-of-mind models;
- free-form generated dialogue or scene performance;
- report propagation outside authored Vela routes;
- generated negotiation terms or unrestricted model prose;
- multiple-resource plan search and independent promise deadlines;
- tactical or real-time off-screen simulation;
- campaign-clock progression outside completed navigation arrivals or explicit director calls;
- repository-backed strategic save integration;
- additional player-facing Vela actors, deeper Solace negotiation, branching requests, and scene performance.

The strategic stack now has a live browser harness, normal Vela and Solace interactions, completed-travel off-screen progression, player-facing return summaries, and a non-obscuring collapsible strategic UI dock. Repository-backed campaign save integration and scene performance remain unimplemented.

## Smallest useful implementation slice

AI-1 through AI-9.4.1 are implemented as a validated strategic stack, live project-scoped browser session, developer inspection panel, player-visible Vela and Solace interactions, exact-once travel progression, receipt-derived return summaries, and a non-obscuring collapsible strategic UI dock. The smallest useful next slice is AI-9.5's repository-backed campaign save integration and end-to-end replay proof:

```text
complete Vela interaction
→ travel and advance off-screen schedules
→ resolve Solace allocation
→ write one campaign save
→ reload the application
→ restore navigation, strategic state, arrival receipts, and acknowledgements
→ reproduce the same next verified result
```

AI-9.5 should promote the existing compatible session envelope into the normal game save surface rather than introducing another strategic serialization format. AI-10 tactical or learned experiments should remain deferred until one durable cross-system campaign replay is proven.

## Research basis

This plan is informed by established and current game-AI work, but the project-specific architecture and requirements above are design decisions for this game.

- Mateas, Michael, and Andrew Stern. “Structuring Content in the Façade Interactive Drama Architecture.” AIIDE 2005. Façade combined reactive character behavior with higher-level story beats and drama management.  
  https://doi.org/10.1609/aiide.v1i1.18722

- Nelson, Mark J., and Michael Mateas. “Search-Based Drama Management in the Interactive Fiction Anchorhead.” AIIDE 2005. This work frames drama management as selecting bounded interventions in response to player behavior.  
  https://doi.org/10.1609/aiide.v1i1.18723

- McCoy, Joshua, et al. “Social Story Worlds With Comme il Faut.” IEEE Transactions on Computational Intelligence and AI in Games. The system models authored social exchanges, character state, and social-history facts.  
  https://www.cs.uky.edu/~sgware/reading/papers/mccoy2014cif.pdf

- Evans, Richard, and Emily Short. “Versu—A Simulationist Storytelling System.” IEEE Transactions on Computational Intelligence and AI in Games. Versu combines social practices, autonomous agents, and utility-based action selection.  
  https://www.cs.uky.edu/~sgware/reading/citation.php?id=evans2014versu

- Mascarenhas, Samuel, et al. “FAtiMA Toolkit—Toward an Effective and Accessible Tool for the Development of Intelligent Virtual Agents and Social Robots.” 2021. FAtiMA provides modular socio-emotional reasoning and explicit author control.  
  https://arxiv.org/abs/2103.03020

- Park, Joon Sung, et al. “Generative Agents: Interactive Simulacra of Human Behavior.” 2023. The architecture uses observation, memory retrieval, reflection, and planning; the paper also reports failures involving memory retrieval and fabricated embellishment.  
  https://arxiv.org/abs/2304.03442

- Unity Technologies. “ML-Agents Toolkit Overview.” ML-Agents supports reinforcement learning, imitation learning, cooperative and competitive multi-agent training, and automated game testing.  
  https://unity-technologies.github.io/ml-agents/ML-Agents-Overview/

- Google DeepMind. “A Generalist AI Agent for 3D Virtual Environments.” SIMA demonstrates instruction-following through visual input and keyboard/mouse actions across multiple 3D games, illustrating both the promise and the additional burden of pixel-level embodied agents.  
  https://deepmind.google/blog/sima-generalist-ai-agent-for-3d-virtual-environments/

The design conclusion is not that one of these systems should be copied. It is that believable sophistication benefits from combining explicit state, private knowledge, goals, planning, social structure, bounded dramatic guidance, reliable action execution, and carefully constrained generative performance.
