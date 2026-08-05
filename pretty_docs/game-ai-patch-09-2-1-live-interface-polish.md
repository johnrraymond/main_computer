# Game AI Patch 09.2.1 — Live Interface Polish

Contract: `game.ai.patch-09-2-1.vela-interface-polish.v1`

Status: implemented presentation-only polish for the player-visible Vela Gate Authority interaction.

## Objective

Improve the first live strategic-AI card after testing it in the real Game Surface without changing the verified decision, authored briefing, strategic schema, project data, or persistence model.

## Changes

### Developer-control separation

The **Strategic AI** developer toggle moves to the upper-left corner. The player-facing Vela Gate channel remains anchored at the lower-right, so a tall completed card no longer collides with the developer control.

### Container-responsive explanation

The Vela card now uses its own inline-size container rather than only the browser viewport.

- Wide card: two explanation columns.
- Narrow card: one readable column.
- Verified outcome: full-width section on wide cards and normal stacked section on narrow cards.
- Outcome rows: two columns when space permits and one column in constrained cards.
- Card height is bounded to the Game Surface and becomes scrollable when necessary.

### Player-facing language

Internal score-component identifiers and raw decimals are no longer rendered.

Mappings include:

```text
goalPriority            → Mission priorities
evidenceSupport         → Available evidence
observationReliability  → Source reliability
```

Influence values are expressed as:

```text
Major influence
Meaningful influence
Supporting influence
Reduced confidence
```

Alternative actions are expressed as:

```text
Strong alternative
Viable alternative
Lower-confidence option
```

The deterministic scores remain in the strategic receipt and developer panel; only the player presentation changes.

### Verified outcome rows

Consequences are presented as labeled rows:

```text
Verification      Accepted by the action verifier
World state       Advanced to revision 1
Shared knowledge  3 Vela actors received updates
Resource used     1 patrol deployment
```

### Completed state

After the briefing is received:

- the action button remains fully legible;
- the button uses a green verified treatment;
- the channel signal turns green and stops pulsing;
- the status block uses a completed-state treatment;
- the interaction remains disabled and idempotent.

Reduced-motion preferences disable the signal animation.

## Strategic boundary

No AI behavior changed.

The patch does not modify:

- `game_projects/*/project.json`;
- strategic schema or state versions;
- actor policies;
- action selection;
- canonical effects;
- communication claims or templates;
- session persistence;
- interaction completion detection.

The same default run still selects `action.vela.move-patrol-to-chiron`, advances canonical revision to `1`, delivers three observations, consumes one patrol deployment, and renders the same safe official briefing.

## Files changed

```text
main_computer/web/applications/apps/webgl.html
main_computer/web/applications/styles/strategic-ai-vela-interaction.css
main_computer/web/applications/styles/strategic-ai-debug.css
main_computer/web/applications/scripts/strategic-ai-vela-interaction.js
tests/test_strategic_ai_vela_live_interaction.py
pretty_docs/game-ai-patch-09-2-1-live-interface-polish.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Acceptance proof

- The player interaction still executes the same verified turn and safe briefing.
- Player labels contain no raw internal metric identifiers.
- Player alternatives contain no raw decimal score display.
- Consequence rows identify verification, revision, shared knowledge, and resource use.
- The card declares an inline-size container and a narrow-card one-column rule.
- The completed button has a dedicated high-contrast state.
- The developer toggle is positioned away from the player card.
- Reopening the completed interaction remains non-mutating.

## Deferred work

AI-9.3 remains the next behavioral slice:

```text
travel between systems
→ advance bounded off-screen schedules exactly once
→ exclude the new active system
→ persist changes
→ show a player-facing return summary
```
