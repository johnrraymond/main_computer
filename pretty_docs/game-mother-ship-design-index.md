# Mother Ship Expansion Design Index

Contract: `game.mother-ship-design-index.v1`

This is the entry point for the design documents that guide expansion of the current shuttle game into an explorable mother ship.

## Documents

| Document | Purpose |
| --- | --- |
| `pretty_docs/game-mother-ship-expansion-design.md` | Overall game design, player fantasy, core loop, first playable slice |
| `pretty_docs/game-mother-ship-deck-layout.md` | Initial deck map, rooms, doors, collision expectations, signage |
| `pretty_docs/game-mother-ship-systems-design.md` | Runtime state, doors, terminals, objectives, HUD, in-game twiddle expectations |
| `pretty_docs/game-mother-ship-implementation-plan.md` | Safe implementation milestones, test plan, risk notes |
| `pretty_docs/game-mother-ship-feature-patch-series.md` | Ordered implementation patch backlog with files, acceptance checks, and verification |
| `pretty_docs/game-mother-ship-bridge-route-plan.md` | Focused route plan for filling out the rest of the ship so the player can reach the bridge |

## Intended use

Use these documents before adding more ship geometry or gameplay systems. The current desired sequence is:

```text
read design index
→ read bridge route plan
→ ensure Bay Ops and Security are visibly connected
→ add Main Corridor Hub signage
→ make Engineering restore bridge power
→ add Bridge Access Vestibule
→ add walkable Bridge Deck and command console
```

## Current implementation boundary

The current game implementation should be treated as:

```text
Shuttle defense
→ pilot mode
→ docking
→ shuttle-bay arrival
→ starboard access to Bay Operations
→ first interior-state doors, terminals, objectives, and branch stubs
```

The remaining bridge work is not just state logic. Every newly reachable region must have matching visible modeling, walkable bounds, location-gated prompts, and objective text.

## Patch discipline

Each future implementation patch should:
- start from the latest uploaded snapshot;
- change the smallest set of runtime files needed for one milestone;
- keep project defaults aligned across `webgl-demo`, `starter-game`, and `new-game`;
- package full replacement files for `new_patch.py`;
- verify with `node --check`, targeted game tests, and `new_patch.py --dry-run`.

