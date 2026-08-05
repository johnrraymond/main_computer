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
| `pretty_docs/game-warp-navigation-definition.md` | Project-level contract and thirty-two-system reference graph for adjacent-route warp travel |
| `pretty_docs/game-warp-navigation-runtime.md` | Executable adjacent-route warp state machine, bridge-console access, movement, and viewscreen presentation |
| `pretty_docs/game-star-system-density-and-choice-contract.md` | Flat warp UI, richer multi-star and multi-habitable systems, concentrated action, and just-in-time major-choice requirements |
| `pretty_docs/game-sophisticated-ai-architecture-plan.md` | Hybrid world, belief, memory, planning, director, decision-receipt, and bounded generative-performance architecture |
| `pretty_docs/game-ai-patch-01-strategic-data-contract.md` | Implemented AI-1 schema, deterministic fixture, cross-reference validator, tests, and explicit non-runtime boundary |
| `pretty_docs/game-ai-patch-02-deterministic-cognition-kernel.md` | Implemented AI-2 observation ingestion, belief revision, memory retrieval, deterministic scoring, receipts, and save restoration |
| `pretty_docs/game-ai-patch-03-action-authority-and-consequences.md` | Implemented AI-3 typed proposals, authority and consequence verification, atomic effects, resources, outcomes, and v2 migration |
| `pretty_docs/game-ai-patch-03-1-strategic-turn-coordination-and-revision-lock.md` | Implemented AI-3.1 canonical revision locks, authored policy profiles, conservative legacy migration, and complete headless turn coordination |
| `pretty_docs/game-ai-patch-04-vela-gate-three-agent-prototype.md` | Implemented AI-4 three-agent Vela Gate prototype with distinct knowledge, proactive action, cross-destination belief updates, strategy revision, and a coherent false-belief decision |
| `pretty_docs/game-ai-patch-05-knowledge-propagation-and-captain-modeling.md` | Implemented AI-5 authored report routes, report ancestry, bounded rumor distortion, public propagation, captain models, and social-state migration |
| `pretty_docs/game-ai-patch-06-solace-reach-coordination-and-promises.md` | Implemented AI-6 Solace Reach finite-resource competition, typed promises, kept-or-broken trust updates, and later cooperation changes |
| `pretty_docs/game-ai-patch-07-bounded-campaign-director.md` | Implemented AI-7 authored major-route opportunities, bounded activation windows, director receipts, reversible selection, and expiry without forced actor actions |
| `pretty_docs/game-ai-patch-08-communicative-intent-and-safe-wording.md` | Implemented AI-8 authored speech acts, knowledge and audience validation, structured-promise wording, constrained adapter selection, and deterministic fallback |
| `pretty_docs/game-ai-patch-09-deterministic-budgeted-offscreen-simulation.md` | Implemented AI-9 active-system exclusion, deterministic schedule budgets, authored report latency, protected deadlines, persistence, and explainable return summaries |
| `pretty_docs/game-ai-patch-09-1-live-strategic-session-and-developer-harness.md` | Implemented AI-9.1 project-scoped browser session ownership, Game Surface navigation binding, live developer controls, and snapshot persistence |
| `pretty_docs/game-ai-patch-09-2-player-visible-vela-gate-interaction.md` | Implemented AI-9.2 player-visible Vela Gate Authority channel, verified official turn, knowledge-safe briefing, and readable decision explanation |
| `pretty_docs/game-ai-patch-09-2-1-live-interface-polish.md` | Implemented AI-9.2.1 responsive Vela interaction layout, player-facing metric labels, verified outcome rows, completed-state styling, and developer-control separation |
| `pretty_docs/game-ai-patch-09-3-travel-triggered-offscreen-progression-and-return-summary.md` | Implemented AI-9.3 exact-once navigation arrival processing, bounded off-screen advancement, active-system exclusion, persistent return baselines, and player-facing while-away summaries |
| `pretty_docs/game-ai-patch-09-4-player-visible-solace-coordination.md` | Implemented AI-9.4 player-visible Solace typed promise, scarce shuttle allocation, kept-or-broken resolution, trust change, and AI-selected Osprey cooperation response |
| `pretty_docs/game-ai-patch-09-4-1-non-obscuring-collapsible-strategic-ui.md` | Implemented AI-9.4.1 shared side dock and bottom drawer, persistent full/compact/collapsed modes, and non-overlap geometry proof for Vela, Solace, and return-summary cards |
| `pretty_docs/game-forty-system-scenario-bible.md` | Campaign spine, scenario-design contract, shared factions, route-story rules, and future state model for thirty-two warp systems and eight absorbed local-world threads |
| `pretty_docs/game-forty-system-route-scenarios.md` | Working identities, conditions, traffic, encounters, and consequence flow for all forty-two authored routes |
| `pretty_docs/game-scenarios-origin-region.md` | Eight Origin warp-system dossiers plus eight absorbed local-world threads |
| `pretty_docs/game-scenarios-meridian-region.md` | Eight Meridian Cluster dossiers: contracts, debt, trade, logistics, and institutional coercion |
| `pretty_docs/game-scenarios-helix-region.md` | Seven Helix Cluster dossiers: science, adaptation, containment, medicine, and unintended consequences |
| `pretty_docs/game-scenarios-crown-region.md` | Three Crown Cluster dossiers: military authority, succession, war memory, and legitimacy |
| `pretty_docs/game-scenarios-verge-region.md` | Six Verge Cluster dossiers: autonomy, frontier survival, ancient infrastructure, and Axiom |
| `pretty_docs/game-patch-aware-content-architecture.md` | Patch-aware content tiers, metadata, acceptance checks, and validation loop |
| `game_projects/schema/game-definition.v1.schema.json` | Machine-readable JSON Schema for future game definition data |
| `game_projects/schema/space-navigation.v1.schema.json` | Machine-readable JSON Schema for project-level warp-navigation data |
| `game_projects/schema/strategic-ai.v1.schema.json` | Machine-readable JSON Schema for strategic actors, facts, evidence, beliefs, memories, actions, checkpoints, and receipts |
| `pretty_docs/game-runtime-patch-B-mother-ship-state-defaults.md` | Patch B implementation note for centralizing mother-ship runtime state defaults without behavior changes |
| `pretty_docs/game-runtime-patch-C-rooms-and-movement.md` | Patch C implementation note for extracting mother-ship rooms, movement bounds, exits, and spawns into level data |
| `pretty_docs/game-runtime-patch-D-interactables.md` | Patch D implementation note for extracting mother-ship terminals, prompts, ranges, and E-key action ids into interactable data |
| `pretty_docs/game-runtime-patch-E-interaction-registry.md` | Patch E implementation note for routing E-key action ids through a safe interaction registry |
| `pretty_docs/game-runtime-patch-F-definition-validators.md` | Patch F implementation note for validating room reachability, interactables, objectives, handlers, and no-locked-door consistency |
| `pretty_docs/game-runtime-patch-G-renderer-decomposition.md` | Patch G implementation note for behavior-preserving renderer constructor seams and future subsystem extraction |
| `pretty_docs/game-runtime-patch-H-content-props.md` | Patch H implementation note for data-defined mother-ship content props and prop reachability validation |
| `pretty_docs/game-runtime-patch-I-content-markers.md` | Patch I implementation note for data-defined map markers that replace repeated hardcoded room marker calls |
| `pretty_docs/game-runtime-patch-J-prop-target-validation.md` | Patch J implementation note for validating map marker, status panel, and route prop targets |
| `pretty_docs/game-runtime-patch-K-interactable-hotspots.md` | Patch K implementation note for rendering E-key interaction hotspots directly from data-defined interactables |
| `pretty_docs/game-runtime-patch-L-interactable-visual-metadata.md` | Patch L implementation note for styling E-key interaction hotspots through normalized interactable visual metadata |
| `pretty_docs/game-runtime-patch-M-terminal-console-props.md` | Patch M implementation note for rendering terminal/console bodies from data-defined props |
| `pretty_docs/game-runtime-patch-N-room-visual-metadata.md` | Patch N implementation note for room visual metadata and data-driven room boundary rendering |
| `pretty_docs/game-runtime-patch-O-room-geometry-extraction.md` | Patch O implementation note for room geometry metadata and data-driven structural rendering |
| `pretty_docs/game-runtime-patch-O1-docking-handoff-void-guard.md` | Patch O.1 corrective note for post-docking input suppression and visible corridor-trunk guard rails |
| `pretty_docs/game-runtime-patch-P-content-defined-viewscreens.md` | Patch P implementation note for content-defined viewscreen props and state-driven display programs |
| `pretty_docs/game-runtime-patch-Q-interaction-effect-metadata.md` | Patch Q implementation note for data-declared interaction effect expectations |
| `pretty_docs/game-runtime-patch-R-validator-test-harness.md` | Patch R implementation note for direct project JSON validation of mother-ship definitions |
| `pretty_docs/game-runtime-patch-S-renderer-module-split.md` | Patch S implementation note for browser-safe shuttle-3D renderer module registry and extracted room-geometry/viewscreen render passes |
| `pretty_docs/game-runtime-patch-T-save-migration-model.md` | Patch T implementation note for explicit mother-ship definition/state versions and load-time migration defaults |
| `pretty_docs/game-runtime-patch-U-polygon-annotation-tool.md` | Patch U implementation note for hold-P live WebGL polygon/object annotation tooling. |
| `pretty_docs/game-runtime-patch-U1-annotation-rendered-primitive-fallback.md` | Corrective note for selecting visible one-off rendered bars/beams with the hold-P annotation tool. |
| `pretty_docs/game-runtime-patch-U2-annotation-modal-input-guard.md` | Corrective note for suspending gameplay keys while the annotation dialog receives text input. |

## Intended use

Use these documents before adding more ship geometry or gameplay systems. The current desired sequence is:

```text
read design index
→ read runtime rearchitecture plan and game definition schema
→ keep the bridge route and warp runtime playable
→ read the star-system density and choice contract before changing destination topology or major route choices
→ read the sophisticated AI architecture plan before adding autonomous social, faction, director, or generated-dialogue behavior
→ read the thirty-two-system scenario bible before inventing destination content
→ choose one dossier as a vertical scenario slice
→ implement local-space structure, scenario state, and persistent consequence together
→ add validators before generalizing the scenario framework
→ expand region by region through data-first patches
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

The thirty-two-system warp graph is authored under `project.metadata.spaceNavigation` and the browser runtime executes adjacent-system travel through the physical bridge navigation console. Solace Reach and Vela Gate each expose two stars and five worlds through the navigation runtime while remaining flat peer destinations. Local travel, interacting multi-world scenarios, and adaptive unchosen-destination evolution remain future work.

AI-1 through AI-9.4.1 are implemented: all three projects share one validated v8 strategic definition; the coordinator preserves cognition, action, report, commitment, director, communication, and off-screen boundaries; the Game Surface owns one persistent live strategic session and developer panel; Vela Gate and Solace Reach expose player-facing strategic interactions; travel advances bounded off-screen schedules exactly once; and Vela, Solace, and return reports now share a non-obscuring side dock or bottom drawer with persistent full, compact, and collapsed modes. Scene performance, repository-backed campaign saves, and tactical or learned policies remain unimplemented.

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

Patch A for that transition is documentation/schema only. It does not change runtime behavior. Patch B centralizes current mother-ship state defaults into one runtime factory while preserving the current bridge route, viewscreen, tactical console, and no-locked-door behavior. Patch C extracts the current room list, movement bounds, exits, and shuttle-bay spawn into explicit level data while keeping the same route playable. Patch D extracts terminals, prompts, interaction ranges, and E-key action ids into `motherShipInterior.interactables` while preserving the current route and bridge/tactical behavior. Patch E routes those action ids through a safe interaction registry so future validators can prove that every prompt has a handler before content ships. Patch Q adds declarative effect expectations (`changesState`, `successStatus`, and `nextObjective`) so a prompt can also describe what a successful handler should visibly change. Patch R adds a direct project-JSON validator harness so authored room reachability, prompt handlers, effect metadata, terminal visibility, objective/display targets, and room-bound placement are tested before the browser runtime loads the scene. Patch S begins the browser-safe renderer module split by moving room-geometry and content-defined viewscreen render passes into separate registered script modules while keeping `scene-viewer.js` method names as delegating seams. Patch U adds an editor-facing annotation tool: hold `P` and click visible geometry to attach notes to stable data-defined targets under `metadata.shuttle3d.polygonAnnotations`.


Patch U.3 makes the polygon annotation workflow disk-backed at the point of action:
clicking **Save annotation** now runs the Game Editor project write path
immediately, rather than requiring a second Save Project click.


Patch U.4 replaces the annotation tool's indirect event-only disk handoff with an
explicit editor callback. The project write request carries an annotation receipt,
and the server rereads `project.json` to confirm the annotation exists before the
dialog reports success.


Patch U.5 closes the standalone WebGL persistence gap. The game surface now
supplies a direct annotation save callback, and the server merges the annotation
into the latest `project.json`, rereads it, and reports the exact write path before
the UI claims success.
