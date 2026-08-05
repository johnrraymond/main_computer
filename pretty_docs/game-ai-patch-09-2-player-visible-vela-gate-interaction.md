# Game AI Patch 09.2 — Player-Visible Vela Gate Interaction

Contract: `game.ai.patch-09-2.vela-live-interaction.v1`

Status: implemented one player-visible Vela Gate strategic interaction using the live project session; automatic travel-triggered off-screen advancement and return summaries remain separate work.

## Objective

Turn the existing Vela Gate headless proof into one normal Game Surface interaction without creating a second state store or bypassing the strategic verifiers.

```text
arrive at Vela Gate
→ open Gate Authority channel
→ activate authored campaign opportunity
→ run one verified official turn
→ render knowledge-safe briefing
→ explain the selected action and alternatives
→ persist the strategic result
```

## Player interaction

While the active navigation system is:

```text
system.vela-gate
```

the Game Surface displays a **Vela Gate Authority** channel card. The card is hidden in every other system.

The player may select:

```text
Request official briefing
```

The interaction then uses the existing live strategic session to:

1. activate `opportunity.campaign.vela-gate-intervention` when it is still available;
2. run `actor.vela.gate-official` through the real coordinator;
3. commit the selected proposal through the action verifier;
4. render `communicative-intent.vela.official-customs-briefing`;
5. present the result in player-facing language.

No strategic schema or project data changed.

## Verified baseline result

From the authored v8 default state and deterministic live-session seed, the official selects:

```text
action.vela.move-patrol-to-chiron
```

Player-facing label:

```text
Move patrol to Chiron
```

The action is accepted and consumes:

```text
resource.vela.patrol-deployment × 1
```

Canonical revision advances from `0` to `1`, and three resulting observations are delivered to the Vela actors.

## Knowledge-safe briefing

The visible briefing is:

```text
Our current assessment is that the customs explanation accounts for the incident.
```

It is rendered through the existing communication runtime. The private survivor suspicion:

```text
communication-claim.vela.organizer-compromised
```

is not part of the official intent and cannot appear in the briefing.

## Explainable decision display

The card exposes a collapsible **Why the Authority acted** section containing:

- selected verified action;
- decision confidence;
- the three largest non-zero scoring signals;
- three authored alternatives with deterministic scores;
- accepted or rejected outcome;
- canonical revision after the action;
- resulting observation count;
- consumed resources.

This is a player-readable explanation, not the unrestricted developer-state dump.

## Idempotence and persistence

The interaction derives completion from persisted strategic receipts and outcomes.

After the first accepted official turn:

- the request button becomes disabled;
- reopening the card does not run another actor turn;
- the briefing is reconstructed through a pure communication preview;
- canonical revision and session sequence do not change;
- leaving and returning to the Game Surface retains the result through the AI-9.1 session owner.

The interaction does not create its own local-storage record.

## Boundary preservation

The Vela-specific orchestration exists only in:

```text
main_computer/web/applications/scripts/strategic-ai-vela-interaction.js
```

The cognition, action, social, commitment, director, communication, coordinator, off-screen, and session runtimes remain scenario-generic.

The presentation controller calls only existing public session operations:

```text
activateCampaignRoute
runActorTurn
performCommunication
```

Canonical facts and resources change only through the verified action outcome.

## Live test procedure

1. Open **Applications → Game Surface**.
2. Travel or set navigation to **Vela Gate**.
3. Confirm the **Vela Gate Authority** channel appears.
4. Select **Request official briefing**.
5. Confirm the official briefing appears.
6. Expand **Why the Authority acted**.
7. Confirm **Move patrol to Chiron**, the confidence value, decision signals, alternatives, and verified consequences.
8. Switch applications or rerender the scene.
9. Return to Game Surface and confirm the interaction remains complete without another revision.

The existing Strategic AI developer panel may be used to inspect the matching decision, proposal, outcome, observations, and canonical revision.

## Files changed

```text
main_computer/web/applications.html
main_computer/web/applications/apps/webgl.html
main_computer/web/applications/styles/strategic-ai-vela-interaction.css
main_computer/web/applications/scripts/strategic-ai-vela-interaction.js
main_computer/web/applications/scripts/webgl-desktop.js
tests/test_strategic_ai_vela_live_interaction.py
pretty_docs/game-ai-patch-09-2-player-visible-vela-gate-interaction.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Not implemented

- Automatic interaction activation on arrival without player input.
- Player choice among multiple official requests.
- Survivor or rescue-organizer player conversations.
- Scene character animation, voice, or camera performance.
- Automatic off-screen advancement during travel.
- Player-facing “while you were away” summaries.
- Repository-backed save slots beyond the existing browser session persistence.
- Tactical or learned AI.

## Next patch

The next bounded slice is AI-9.3:

```text
leave active system
→ advance newly off-screen schedules with explicit budget
→ preserve the new active system from simulation
→ return later
→ show an explainable player-facing change summary
→ deterministic save and restore
```
