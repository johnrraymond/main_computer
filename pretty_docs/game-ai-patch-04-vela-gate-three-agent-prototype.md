# Game AI Patch 04 — Vela Gate Three-Agent Prototype

Contract: `game.ai.patch-04.vela-gate-three-agent-prototype.v1`

Status: implemented headless Vela Gate strategic prototype; not connected to scene interaction or player-facing dialogue.

## Objective

Use the existing strategic cognition, authority, consequence, revision-lock, and turn-coordination runtimes to prove that three authored characters can:

- begin with different private knowledge;
- act from different goals and policy profiles;
- initiate consequential action without a player prompt;
- react to reliable observations;
- change strategy;
- make a coherent mistake from a false belief;
- affect actors at other Vela Gate destinations;
- remain deterministic and fully receipt-backed.

No Vela-specific conditional logic is added to the cognition or action runtimes.

## Strategic actors

### Gate Authority Official

Location:

```text
destination.vela-gate.velaris-orbit
```

Active goals:

- restore safe navigation;
- preserve Gate Authority legitimacy.

Private interpretation:

- internal traffic gaps suggest deliberate beacon corruption;
- the official still gives substantial confidence to the public customs explanation, even though canonical state marks that explanation incomplete.

Candidate actions:

- move a patrol to Chiron;
- offer quiet corridor repair;
- seal selected traffic records.

The authored policy favors moving the patrol before the captain speaks.

### Rescue-Guild Organizer

Location:

```text
destination.vela-gate.seraph-relay
```

Active goals:

- recover missing civilians;
- protect witnesses;
- expose deliberate beacon corruption.

Private interpretation:

- disappearance patterns conflict with official claims;
- the available witness report is initially incomplete and only moderately reliable;
- the survivor probably holds useful evidence.

Candidate actions:

- leak authenticated beacon evidence;
- restrict witness access;
- request mother-ship rescue.

The authored policy protects witness access under weak reports, but shifts to public disclosure when reliable corroboration arrives.

### Beacon-Log Survivor

Location:

```text
destination.vela-gate.chiron-observatory
```

Active goals:

- survive;
- protect the beacon log;
- avoid compromised custody.

Private interpretation:

- the survivor directly witnessed beacon sabotage;
- the survivor holds the surviving beacon log;
- the survivor strongly believes a false report that the rescue organizer is compromised.

Candidate actions:

- refuse rescue-guild contact;
- deliver evidence to the guild;
- signal the mother ship.

The survivor therefore makes a coherent but incorrect decision to refuse guild contact.

## Knowledge separation

Canonical state says:

```text
beacon corruption was deliberate
the public customs explanation is incomplete
the rescue organizer is not compromised
the evidence is not public
the patrol has not yet moved to Chiron
the survivor holds the beacon log
the captain retained an evidence copy
```

No actor begins with all of those facts as private beliefs.

Each actor has separate:

- observations;
- beliefs;
- memories;
- goals;
- location;
- authority;
- action vocabulary;
- policy profile.

The false organizer-compromise report remains an evidence-backed private belief. It does not alter canonical truth.

## Typed actions and consequences

The prototype adds ten Vela Gate action types and fifteen effect types.

### Official actions

`action.vela.move-patrol-to-chiron`

- requires patrol-deployment authority;
- is allowed only at Velaris Gate Central;
- requires the patrol not already to be at Chiron;
- consumes one patrol-deployment resource;
- sets the canonical patrol-at-Chiron fact;
- records a canonical event;
- creates resulting observations for all three actors.

`action.vela.offer-quiet-corridor-repair`

- requires repair-offer authority;
- consumes one corridor-repair credit;
- sets the canonical repair-offered fact;
- records the private concession;
- notifies the official and organizer.

`action.vela.seal-traffic-records`

- requires record-sealing authority;
- requires records not already sealed;
- sets the canonical records-sealed fact;
- records the decision;
- notifies the official.

`action.vela.inspect-traffic-gaps`

- requires internal-record inspection authority;
- records a focused inspection;
- returns a typed observation about deliberate beacon corruption to the official.

### Organizer actions

`action.vela.leak-beacon-evidence`

- requires evidence-disclosure authority;
- requires the evidence not already to be public;
- consumes one authenticated disclosure window;
- makes the evidence public;
- records the disclosure;
- creates observations for all three actors.

`action.vela.restrict-witness-access`

- records a reversible witness-protection restriction;
- notifies the organizer and survivor.

`action.vela.request-mother-ship-rescue`

- records a rescue request for the Chiron corridor;
- notifies the organizer and survivor.

### Survivor actions

`action.vela.refuse-rescue-guild-contact`

- records the survivor's refusal;
- notifies the survivor and organizer.

`action.vela.deliver-evidence-to-guild`

- requires the survivor to remain the canonical evidence holder;
- transfers canonical custody to the organizer;
- records the transfer;
- notifies both actors.

`action.vela.signal-mother-ship`

- records a narrow rescue and evidence signal;
- notifies the survivor and official.

All consequences pass through the existing action verifier and atomic commit boundary.

## Cross-actor observation delivery

The turn coordinator now groups accepted resulting observations by their authored observer.

```text
accepted action
→ resulting observation ids
→ group by observerId
→ update each observer's private beliefs
→ persist one shared final state
```

The acting actor's prior `resultingBeliefUpdates` field remains available. A new `resultingBeliefUpdatesByActor` map exposes all observers updated by that turn.

Rejected actions still create no resulting observation or belief update.

## Demonstrated behaviors

### Proactive action

With no incoming player observation:

```text
Gate Authority Official
→ selects move patrol to Chiron
→ proposal passes validation
→ patrol resource is consumed
→ patrol fact becomes true
→ official, organizer, and survivor receive observations
```

This action originates at Velaris Gate Central and changes private state at Seraph Relay and Chiron Observatory.

### Strategy revision

On the untouched opening state, the organizer selects:

```text
restrict witness access
```

After receiving reliable authenticated evidence that the captain retained an evidence copy, the same actor, state definition, and seed select:

```text
leak beacon evidence
```

The changed choice is visible in the candidate scores and decision receipt.

The official's patrol move also supplies reliable cross-destination corroboration. An organizer turn after that action selects the evidence leak instead of the baseline witness restriction.

### Coherent false-belief action

Canonical state:

```text
rescue organizer compromised = false
```

Survivor belief:

```text
rescue organizer compromised = true
confidence = 0.88
basis = compromised-organizer rumor observation
```

The survivor's policy and receipt select:

```text
refuse rescue-guild contact
```

The action is wrong about the organizer but coherent with the survivor's provenance-backed private state.

## Determinism and persistence

The focused prototype proves:

- identical definition, state, and seed produce the same official turn;
- the official-to-organizer sequence is deterministic;
- restoring after the official turn reproduces the same organizer decision, proposal, outcome, observations, beliefs, and canonical state;
- generated single-actor and multi-actor states remain JSON-Schema valid;
- generated states remain valid under the Python cross-reference validator.

## Files changed

```text
game_projects/new-game/project.json
game_projects/starter-game/project.json
game_projects/webgl-demo/project.json
main_computer/web/applications/scripts/strategic-ai-coordinator.js
tests/test_strategic_ai_definition_contract.py
tests/test_strategic_ai_action_runtime.py
tests/test_strategic_ai_vela_gate.py
pretty_docs/game-ai-patch-04-vela-gate-three-agent-prototype.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Explicit exclusions

This patch does not add:

- scene-viewer instantiation;
- local travel controls;
- player-facing conversations;
- generated dialogue;
- a generalized report-propagation network;
- a learned captain model;
- theory of mind;
- faction-wide off-screen simulation;
- campaign direction;
- Vela-specific branches in the core cognition or action runtimes.

The captain-retained-evidence input is one authored, typed observation used to prove plan revision. It is not a general captain-model implementation.

## Next patch

The next bounded slice is AI-5: knowledge propagation and captain modeling.

It should add:

```text
private or public report
→ authorized transmission path
→ provenance-preserving recipient observation
→ confidence and distortion rules
→ recipient-specific captain interpretation
→ later action change
```

That work should reuse the three Vela Gate actors and preserve the current distinction between canonical facts, observations, beliefs, and generated performance.
