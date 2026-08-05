# Game AI Patch 09.3 — Travel-Triggered Off-Screen Progression and Return Summary

Contract: `game.ai.patch-09-3.travel-offscreen-return.v1`

Status: implemented exact-once strategic progression after completed inter-system travel and a persisted player-facing return summary.

## Objective

Connect the existing space-navigation arrival receipt to the existing deterministic off-screen scheduler without introducing wall-clock background work or bypassing strategic verification.

```text
complete inter-system travel
→ identify the committed arrival exactly once
→ mark the departed system off-screen
→ advance strategic time to navigation world time
→ exclude the newly active system
→ persist the simulation receipt
→ show changes when the player later returns
```

## Arrival identity

A completed arrival is identified by the stable tuple:

```text
lastCompletedRouteId
lastArrivalAtMs
currentSystemId
elapsedWorldTime
```

The tuple is persisted as an arrival key in the project-scoped strategic session. Repeated navigation callbacks, scene rerenders, application switching, browser restoration, and snapshot import cannot process the same arrival twice.

The integration does not use timers, intervals, wall-clock polling, or autonomous background execution.

## Travel progression

The integration runs only when navigation reports:

```text
travelPhase = in-system
travelling = false
lastCompletedRouteId is present
lastArrivalAtMs is present
```

Course plotting, warp charging, in-warp frames, arrival presentation frames, and unchanged in-system frames do not advance strategic time.

At a committed arrival:

1. the system being left receives a stored return-summary baseline;
2. the navigation world time becomes the scheduler target time;
3. the destination becomes the active strategic system;
4. the destination schedule is excluded;
5. the existing four-unit authored budget is applied;
6. all effects still pass through the existing coordinator and subsystem verifiers;
7. the arrival and simulation receipt are persisted together.

## Deterministic Solace-to-Vela proof

Travel from Solace Reach to Vela Gate at world time `4`:

```text
active system: Vela Gate
excluded schedule: Vela Gate opening
budget: 4
```

The off-screen Solace schedule completes:

```text
create typed shuttle promise       cost 1
render promise wording             cost 1
allocate rescue shuttle            cost 2
```

The action verifier commits the shuttle allocation, the structured promise resolves as kept, and canonical revision advances from `0` to `1`.

Repeating the same arrival callback:

```text
reused = true
off-screen receipt count unchanged
session sequence unchanged
canonical revision unchanged
```

## Deterministic Vela-to-Solace proof

Travel back to Solace Reach at world time `8`:

```text
active system: Solace Reach
excluded schedule: Solace Reach relief
budget: 4
```

The off-screen Vela schedule processes:

```text
activate intervention window        cost 1
run verified traffic inspection     cost 2
deliver sourced rumor               cost 1
```

The one-unit official briefing remains pending because the authored budget is exhausted.

## Player-facing return summary

When the player returns to a system that has a stored departure baseline, the session compares the baseline with the current existing off-screen summary.

For the first return to Solace Reach, the player sees:

```text
While you were away — Solace Reach

Create the typed rescue-shuttle promise
Completed at strategic time 1

Render safe promise wording
Completed at strategic time 2

Run the verified shuttle allocation
Completed at strategic time 3
```

The text comes from authored schedule descriptions. Status, completion time, reason, result ancestry, and canonical revision come from persisted scheduler state.

The presentation layer does not invent consequences or summarize unverified model prose.

## Acknowledgement and persistence

The player may dismiss the return report. Acknowledgement is persisted in the strategic-session envelope.

Snapshot export and restore preserve:

- processed arrival keys;
- per-system absence baselines;
- current return notice;
- acknowledgement state;
- strategic state and off-screen receipts.

A restored session therefore neither repeats travel progression nor reopens an acknowledged report.

## Strategic boundary

No strategic schema or project definition changed.

The travel layer calls only:

```text
StrategicAISession.completeTravel
StrategicAIOffscreenRuntime.simulateUntil
StrategicAIOffscreenRuntime.getReturnSummary
```

The scheduler continues to delegate action, report, commitment, director, and communication work through the existing coordinator APIs.

The newly active system is never simulated off-screen.

## Browser integration

`webgl-desktop.js` forwards real Game Surface navigation snapshots to the travel integration controller.

The controller:

- handles completed arrivals;
- performs ordinary active-system synchronization for non-arrival navigation;
- binds the player-facing return panel to the current project session;
- contains no timers or independent state store.

## Files changed

```text
main_computer/web/applications.html
main_computer/web/applications/apps/webgl.html
main_computer/web/applications/styles/strategic-ai-travel-integration.css
main_computer/web/applications/scripts/strategic-ai-session.js
main_computer/web/applications/scripts/strategic-ai-travel-integration.js
main_computer/web/applications/scripts/webgl-desktop.js
tests/test_strategic_ai_travel_integration.py
pretty_docs/game-ai-patch-09-3-travel-triggered-offscreen-progression-and-return-summary.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Live test procedure

1. Reset the strategic session at Solace Reach.
2. Use normal navigation to travel to Vela Gate.
3. Confirm strategic time advances to navigation world time `4`.
4. Confirm Vela Gate is excluded and the three Solace steps complete.
5. Return to Solace Reach.
6. Confirm the **While you were away — Solace Reach** panel appears.
7. Confirm the three authored developments and completion times.
8. Dismiss the report.
9. Switch applications or reload a compatible snapshot.
10. Confirm the same arrivals do not process again and the acknowledged report stays dismissed.

## Deferred work

- A player-facing Solace Reach coordination encounter.
- Return-summary linking to scene terminals or character performance.
- More than two authored off-screen systems.
- Repository-backed campaign save slots.
- Real-time or tactical off-screen simulation.
- Learned policies.

## Next patch

The next bounded slice is AI-9.4:

```text
open Solace Reach relief channel
→ create or inspect the structured shuttle promise
→ expose the scarce-resource choice
→ commit one verified allocation
→ show kept or broken commitment consequences
→ retain deterministic state across travel
```
