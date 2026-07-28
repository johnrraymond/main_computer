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
| `pretty_docs/game-runtime-rearchitecture-plan.md` | Architecture plan for moving games from hardcoded renderer behavior to a data-driven runtime |
| `pretty_docs/game-definition-schema-v1.md` | Human-readable guide to the first game definition schema |
| `pretty_docs/game-patch-aware-content-architecture.md` | Patch-aware content tiers, metadata, acceptance checks, and validation loop |
| `game_projects/schema/game-definition.v1.schema.json` | Machine-readable JSON Schema for future game definition data |
| `pretty_docs/game-runtime-patch-F-definition-validators.md` | Patch F implementation note for validating room reachability, interactables, objectives, handlers, and no-locked-door consistency |
| `pretty_docs/game-runtime-patch-G-renderer-decomposition.md` | Patch G implementation note for behavior-preserving renderer constructor seams and future subsystem extraction |
| `pretty_docs/game-runtime-patch-H-content-props.md` | Patch H implementation note for data-defined mother-ship content props and prop reachability validation |
| `pretty_docs/game-runtime-patch-I-content-markers.md` | Patch I implementation note for data-defined map markers that replace repeated hardcoded room marker calls |
| `pretty_docs/game-runtime-patch-J-prop-target-validation.md` | Patch J implementation note for validating map marker, status panel, and route prop targets |
| `pretty_docs/game-runtime-patch-K-interactable-hotspots.md` | Patch K implementation note for rendering E-key interaction hotspots directly from data-defined interactables |
| `pretty_docs/game-runtime-patch-L-interactable-visual-metadata.md` | Patch L implementation note for styling E-key interaction hotspots through normalized interactable visual metadata |
- [Patch M: Terminal Console Props](game-runtime-patch-M-terminal-console-props.md)
| `pretty_docs/game-runtime-patch-N-room-visual-metadata.md` | Patch N implementation note for room visual metadata and data-driven room boundary rendering |
| `pretty_docs/game-runtime-patch-O-room-geometry-extraction.md` | Patch O implementation note for room geometry metadata and data-driven structural rendering |
| `pretty_docs/game-runtime-patch-O1-docking-handoff-void-guard.md` | Patch O.1 corrective note for post-docking input suppression and visible corridor-trunk guard rails |
| `pretty_docs/game-runtime-patch-P-content-defined-viewscreens.md` | Patch P implementation note for content-defined viewscreen props and state-driven display programs |
| `pretty_docs/game-runtime-patch-B-mother-ship-state-defaults.md` | Patch B implementation note for centralizing mother-ship runtime state defaults without behavior changes |
| `pretty_docs/game-runtime-patch-C-rooms-and-movement.md` | Patch C implementation note for extracting mother-ship rooms, movement bounds, exits, and spawns into level data |
| `pretty_docs/game-runtime-patch-D-interactables.md` | Patch D implementation note for extracting mother-ship terminals, prompts, ranges, and E-key action ids into interactable data |
| `pretty_docs/game-runtime-patch-E-interaction-registry.md` | Patch E implementation note for routing E-key action ids through a safe interaction registry |

## Intended use

Use these documents before adding more ship geometry or gameplay systems. The current desired sequence is:

```text
read design index
→ read runtime rearchitecture plan
→ read game definition schema
→ keep the bridge route playable
→ extract current rooms, bounds, exits, terminals, and objectives into data
→ add validators before adding more ship content
→ resume content expansion through data-first patches
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


## Architecture transition

The mother-ship content has reached the point where new features should be planned against the data-driven runtime architecture instead of continuing to add one-off renderer branches. Future implementation patches should preserve the existing playable route while moving state, rooms, interactables, objectives, and validation into explicit game-definition data.

Patch A for that transition is documentation/schema only. It does not change runtime behavior. Patch B centralizes current mother-ship state defaults into one runtime factory while preserving the current bridge route, viewscreen, tactical console, and no-locked-door behavior. Patch C extracts the current room list, movement bounds, exits, and shuttle-bay spawn into explicit level data while keeping the same route playable. Patch D extracts terminals, prompts, interaction ranges, and E-key action ids into `motherShipInterior.interactables` while preserving the current route and bridge/tactical behavior. Patch E routes those action ids through a safe interaction registry so future validators can prove that every prompt has a handler before content ships.
