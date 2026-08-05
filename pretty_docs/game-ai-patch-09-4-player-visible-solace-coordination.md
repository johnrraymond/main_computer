# Game AI Patch 09.4 — Player-Visible Solace Reach Coordination

Contract: `game.ai.patch-09-4.solace-live-coordination.v1`

Status: implemented one player-visible Solace Reach coordination encounter using the existing typed commitment, scarce resource, action verifier, communication runtime, cooperation model, and project-scoped live session.

## Objective

Expose the authored Solace resource conflict through normal Game Surface interaction without adding a second decision system or bypassing strategic verification.

```text
open Solace relief channel
→ create typed Osprey shuttle promise
→ render safe promise wording
→ expose the single-shuttle allocation conflict
→ commit one verified allocation
→ resolve promise as kept or broken
→ let Osprey choose later cooperation from updated trust
→ persist the complete result
```

## Player interaction

The **Solace Reach** relief card appears only while the active system is:

```text
system.solace-reach
```

The card begins with the authored state:

```text
rescue shuttle available: 1
shuttle location: Haven orbit
Osprey trust in Haven: 0.55
```

Selecting **Open coordination and make the Osprey promise** creates:

```text
commitment.solace.shuttle-to-osprey
```

through the commitment runtime and renders:

```text
Because the Osprey evacuation is critical, my commitment Promise the rescue shuttle to Osprey is in force.
```

The wording is bound to the returned structured commitment ID.

## Player allocation choice

The player chooses one of two existing verified actor paths.

### Honor the Osprey promise

```text
Haven coordinator turn
→ action.solace.allocate-shuttle-osprey
→ action verifier accepts
→ shuttle quantity becomes 0
→ destination becomes Osprey Anchorage
→ promise resolves kept
→ Osprey trust becomes 0.91
→ Osprey captain chooses action.solace.share-osprey-manifests
→ canonical revision becomes 2
```

Player-facing outcome:

```text
Promise: Kept
Shuttle allocation: Allocate rescue shuttle to Osprey
Osprey trust: 91% — High trust
Osprey response: Share Osprey civilian manifests
World state: Advanced to revision 2
```

### Divert the shuttle to Lyria

```text
Lyria medic turn
→ action.solace.claim-shuttle-lyria
→ emergency authority and precondition verified
→ shuttle quantity becomes 0
→ destination becomes Lyria medical transfer
→ pledged resource is diverted
→ promise resolves broken
→ Osprey trust becomes 0.11
→ Osprey captain chooses action.solace.withhold-osprey-manifests
→ canonical revision becomes 2
```

Player-facing outcome:

```text
Promise: Broken
Shuttle allocation: Claim rescue shuttle for Lyria
Osprey trust: 11% — Trust damaged
Osprey response: Withhold Osprey civilian manifests
World state: Advanced to revision 2
```

## Authority and AI boundary

The UI does not set commitment status, trust, facts, resources, or Osprey behavior.

It calls only existing session operations:

```text
createCommitment
performCommunication
runActorTurn
```

The two allocation choices select which already-authored actor receives a turn. Each actor still selects its own authored action through the cognition runtime and commits through the action verifier.

Osprey cooperation is not selected by the player interface. The captain receives a normal turn after promise resolution, and the existing `commitmentTrust` policy term selects manifest sharing or withholding.

## Persistence and idempotence

Completion is reconstructed from strategic commitments, outcomes, cooperation models, canonical resources, and receipts.

After either branch completes:

- the allocation choices disappear;
- reopening the card does not run another turn;
- the consumed shuttle cannot be allocated again;
- trust and Osprey response remain visible;
- compatible session snapshot restoration reproduces the same completed branch;
- no separate Solace interaction state store is created.

A partially completed state with a resolved promise but no Osprey outcome exposes **Request the Osprey captain’s response** rather than silently inventing a result.

## Responsive presentation

The Solace card uses an inline-size container.

- Resource and trust state appear in two columns when wide.
- Allocation choices appear side by side when wide.
- Resource, choice, and outcome sections stack when the card is narrow.
- The card is bounded to the Game Surface and becomes scrollable when required.
- Kept and broken branches use distinct trust and status treatments.
- Reduced-motion preferences disable pulse and trust-bar transitions.

## Files changed

```text
main_computer/web/applications.html
main_computer/web/applications/apps/webgl.html
main_computer/web/applications/styles/strategic-ai-solace-interaction.css
main_computer/web/applications/scripts/strategic-ai-solace-interaction.js
main_computer/web/applications/scripts/webgl-desktop.js
tests/test_strategic_ai_solace_live_interaction.py
pretty_docs/game-ai-patch-09-4-player-visible-solace-coordination.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Not changed

- strategic schema and state version;
- project definitions;
- commitment, action, social, communication, coordinator, off-screen, travel, and session kernels;
- authored actors, policies, resources, claims, templates, or schedules;
- Vela Gate interaction;
- travel-triggered off-screen progression;
- return-summary behavior.

## Acceptance proof

- The card is hidden outside Solace Reach.
- The first step creates one typed promise and knowledge-safe wording.
- Kept and broken branches consume exactly one shared shuttle through verified actor actions.
- The commitment resolves from the real allocation outcome.
- Trust changes to the authored `0.91` or `0.11`.
- Osprey independently selects sharing or withholding from the updated trust model.
- Canonical revision reaches `2` in both branches.
- Replaying a completed branch is non-mutating.
- Export and restore preserve the branch.
- Existing strategic regression behavior remains intact.

## Next bounded slice

AI-9.5 should connect the browser-owned strategic session to the repository’s normal campaign save/load surface and prove one end-to-end Vela–travel–Solace replay across a durable save slot.

```text
play Vela interaction
→ travel and process off-screen schedules
→ resolve Solace coordination
→ write campaign save
→ reload application
→ restore the exact strategic and navigation state
→ reproduce the same next verified result
```
