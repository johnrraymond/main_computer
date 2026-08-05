# Game AI Patch 09.1 — Live Strategic Session and Developer Harness

Contract: `game.ai.patch-09-1.live-strategic-harness.v1`

Status: implemented browser integration and developer-facing live controls; not yet a player-facing Vela story interaction or automatic travel-driven simulation.

## Objective

Move the completed AI-1 through AI-9 headless stack into a persistent browser session that can be exercised from the normal Game Surface application.

```text
project.json strategic definition
→ one project-scoped strategic session
→ live verified operations
→ persistent strategic snapshot
→ browser inspection and import/export
```

This slice does not add new strategic facts, actions, actors, schedules, or schema versions. It uses the existing `game.strategicAI.definition.v8` and `game.strategicAI.state.v8` contracts unchanged.

## Runtime owner

The new browser-safe owner is:

```text
main_computer/web/applications/scripts/strategic-ai-session.js
```

It creates and retains exactly one current session for a project definition fingerprint.

The session owns:

- the v8 strategic state;
- the deterministic seed;
- current active-system identity;
- local persistence;
- snapshot import and export;
- coordinator construction;
- off-screen runtime construction;
- a small operation sequence and result log boundary.

Repeated rendering of the same Game Surface project returns the existing session instead of reconstructing state from project defaults.

## Persistence boundary

The default storage key is:

```text
main-computer.strategic-ai.session.v1:<project-id>
```

The stored envelope includes:

- session schema;
- project id;
- strategic definition and state versions;
- deterministic definition fingerprint;
- seed;
- active system;
- operation sequence;
- last operation summary;
- complete strategic state.

A stored snapshot is ignored when its definition fingerprint no longer matches the loaded project. Explicit import rejects:

```text
snapshot-project-mismatch
snapshot-definition-mismatch
snapshot-json-invalid
state-invalid
```

This prevents an old browser snapshot from silently binding to a changed strategic definition.

## Navigation connection

The Game Surface now forwards real navigation snapshots to the live session.

```text
space-navigation currentSystemId
→ strategic session activeSystemId
```

The callback updates only when the current system actually changes. Routine navigation HUD frames do not create repeated strategic operations.

This slice records the active system for developer operations. It does not yet automatically call the off-screen scheduler when warp completes.

## Developer inspection panel

The Game Surface now exposes a `Strategic AI` button. It opens:

```text
main_computer/web/applications/apps/webgl.html
→ #strategic-ai-debug-panel
```

The panel can perform real operations through the session owner:

- run a verified actor turn;
- select the active system;
- advance deterministic off-screen time with a bounded budget;
- activate, deactivate, or expire campaign opportunities;
- create authored typed commitments;
- render knowledge-safe authored communication;
- export a full session snapshot;
- import a compatible snapshot;
- reset to project defaults.

It displays:

- project and state versions;
- active system;
- canonical revision;
- actor, observation, belief, report, and commitment counts;
- campaign and off-screen status counts;
- the complete current strategic state;
- latest result;
- operation log and result identifiers.

The panel does not call lower-level action or state mutation helpers directly. Every operation goes through `StrategicAITurnCoordinator` or `StrategicAIOffscreenRuntime`.

## Browser integration

The project loading path now performs:

```text
Game Surface project read
→ strategic definition discovery
→ MainComputerStrategicAISession.ensure(...)
→ panel binding
→ scene rendering
```

The live editor mirror path performs the same `ensure` operation. A scene change or app switch does not reset strategic state.

The browser globals are:

```javascript
MainComputerStrategicAISession
MainComputerStrategicAIDebugPanel
MainComputerWebglStrategicSession
```

`MainComputerWebglStrategicSession.current()` returns the live project session.

## Live test procedure

1. Start the Main Computer application normally.
2. Open **Game Surface**.
3. Wait for the project scene to load.
4. Click **Strategic AI** in the upper-right corner.
5. Confirm the panel reports:
   - project `webgl-demo`;
   - state `game.strategicAI.state.v8`;
   - active system `system.solace-reach`;
   - canonical revision `0` on a clean reset.
6. Select `Fixture Watch Officer` and choose **Run verified turn**.
7. Confirm:
   - canonical revision advances;
   - one decision receipt, proposal, and outcome appear;
   - latest result shows a `turn.runtime...` identifier.
8. Close the panel, switch applications, return to Game Surface, and reopen it.
9. Confirm the revision and records remain.
10. Choose **Export**, then **Reset defaults**, then **Import**.
11. Confirm the exported revision and active system are restored.
12. Select an off-screen target time and budget to run the existing AI-9 scheduler.

## Automated proof

The focused live-session tests prove:

- all strategic session and panel scripts parse;
- the runtime include order loads coordinator and off-screen APIs before the session owner;
- a real verified actor turn commits through the existing action runtime;
- a same-project re-render returns the identical session object;
- canonical state survives the re-render;
- active-system state survives a session reload through local persistence;
- export, reset, and import restore the same verified state;
- foreign-project and stale-definition snapshots are rejected;
- the panel exposes the required live controls;
- Game Surface project loading binds the project, panel, and navigation callback to one session.

The existing strategic and navigation tests continue to execute the same v8 kernels.

## Files changed

```text
main_computer/web/applications.html
main_computer/web/applications/apps/webgl.html
main_computer/web/applications/styles/strategic-ai-debug.css
main_computer/web/applications/scripts/strategic-ai-session.js
main_computer/web/applications/scripts/strategic-ai-debug-panel.js
main_computer/web/applications/scripts/webgl-desktop.js
main_computer/web/applications/scripts/scene-viewer.js
tests/test_strategic_ai_live_session.py
pretty_docs/game-ai-patch-09-1-live-strategic-session-and-developer-harness.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Explicit limitations

- Strategic state is not yet part of the repository-backed game-save API.
- Warp arrival does not automatically advance off-screen strategic time.
- No Vela actor is yet connected to a player interaction, terminal, or viewscreen.
- No player-facing “while you were away” summary is displayed.
- Communication results remain developer-panel output, not scene performance.
- This is a live developer harness, not a final player interface.
- No wall-clock background execution occurs.

## Next patch

The next bounded slice is AI-9.2, one player-visible Vela Gate vertical slice:

```text
arrive at Vela Gate
→ activate authored campaign opportunity
→ run official verified turn
→ render knowledge-safe briefing
→ show decision evidence and unknowns
→ preserve state across leaving and returning
```

Travel-triggered off-screen advancement and player-facing return summaries should follow as AI-9.3 after the Vela interaction proves the session boundary in normal gameplay.
