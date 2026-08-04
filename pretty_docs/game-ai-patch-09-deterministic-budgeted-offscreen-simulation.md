# Game AI Patch 09 — Deterministic Budgeted Off-Screen Simulation

Contract: `game.ai.patch-09.offscreen-simulation.v1`

Status: implemented headless strategic scheduler and persistence; no live scene simulation, tactical combat, autonomous background execution, or learned policy.

## Objective

Run coarse strategic updates only for systems outside the active system while preserving every existing authority boundary.

```text
authored schedule
→ due time and route latency
→ explicit cost budget
→ existing coordinator boundary
→ persisted result ids and run receipt
→ explainable return summary
```

## Contract version

The schema family remains `game.strategicAI.v1`.

```text
definitionVersion: game.strategicAI.definition.v8
stateVersion: game.strategicAI.state.v8
```

State v8 adds:

- `offscreenSimulationTime`;
- `offscreenStepStates`;
- `offscreenSimulationReceipts`.

Definitions add:

- `offscreenSimulationBudget`;
- `offscreenSchedules`;
- typed actor-turn, report, commitment, director, and communication steps.

Migration from state v1 through v7 creates no historical simulation. It initializes the authored steps as pending, starts the clock at zero, and creates no run receipts.

## Runtime

The generic browser-safe runtime is:

```text
main_computer/web/applications/scripts/strategic-ai-offscreen-runtime.js
```

It loads after the strategic coordinator and before the scene viewer.

The runtime contains no Vela Gate or Solace Reach identifiers. Scenario behavior is supplied only by authored schedule data.

## Budget

The declared maximum budget per run is:

```text
4 cost units
```

A caller may request a smaller value. A larger request is capped at four.

Each step has an integer cost. Ready steps are ordered by:

1. authored ready time;
2. schedule id;
3. step id.

A step that cannot fit in the remaining budget remains pending and is listed in the run receipt as deferred.

## Active-system exclusion

`simulateUntil()` requires the current active system id.

Schedules for that system are never processed. Their state remains pending and the schedule id is listed in `skippedScheduleIds`.

This keeps off-screen simulation from competing with the live system.

## Timing

Every step has `dueAt`.

Report readiness additionally includes the report route’s authored latency:

```text
readyAt = dueAt + route.latency
```

Protected actor turns use:

```text
readyAt = max(dueAt, deadlineAt)
```

The Vela rumor is authored at time 1 on a route with latency 2. It cannot be delivered before time 3.

## Existing boundaries

The scheduler performs no direct canonical mutation.

### Actor turn

An actor-turn step supplies an authored allowlist. All other candidate actions are unavailable for that turn.

The normal cognition, proposal, authority, revision, precondition, resource, effect, and consequence runtimes still execute.

### Report

A report step calls the coordinator’s report-delivery API. The social runtime validates route, sender, recipient, source observation, visibility, distortion, and latency before recipient belief updates.

### Commitment

A commitment step calls the existing typed commitment runtime. It cannot create a promise with unauthorized parties, authority, action, or unavailable resource.

### Director

A director step uses activation, deactivation, or expiry APIs and receives ordinary director receipts and observations.

### Communication

A communication step uses the knowledge-safe communication runtime. A promise communication may bind to the result of an earlier commitment step in the same schedule.

## Protected irreversible work

An actor-turn step that can select any protected effect must include:

- an explicit non-negative deadline;
- every action authority;
- every protected-effect authority.

The runtime rejects the authored schedule before simulation when either condition is missing.

A synthetic protected fixture proves:

```text
missing deadline and authority
→ schedule-invalid

deadline = 5 and required authority present
→ no processing at time 4
→ verified protected action at time 5
```

## Authored Vela schedule

```text
time 0, cost 1:
activate the Vela campaign opportunity

time 1, cost 2:
gate official inspects traffic gaps

sent time 1, route latency 2, cost 1:
survivor rumor reaches the gate official at time 3

time 4, cost 1:
knowledge-safe official briefing
```

Results include a director receipt, verified action outcome, provenance-preserving report, communication id, belief updates, and canonical revision advancement only from the verified action.

## Authored Solace schedule

```text
time 1, cost 1:
create typed shuttle promise

time 2, cost 1:
render promise wording bound to that commitment

time 3, cost 2:
verify and commit the shuttle allocation
```

With a two-unit run budget, the promise and wording complete while the shuttle allocation is deferred. A later run processes the allocation, resolves the promise as kept, and advances the canonical revision.

## Persistence and explanation

Every authored step persists:

- schedule and step ids;
- status;
- attempt count;
- ready and completion times;
- result ids;
- stable reason.

Every simulation run persists:

- from and to times;
- active system;
- declared effective budget;
- consumed budget;
- processed and deferred step ids;
- skipped active-system schedules;
- canonical revision before and after.

`getReturnSummary(systemId)` returns schedule labels, step descriptions, statuses, reasons, timing, result ids, and current canonical revision.

## Verification

Focused tests prove:

- runtime load order and JavaScript syntax;
- generic runtime with no scenario branches;
- active-system exclusion;
- hard budget capping and deferral;
- authored report latency;
- ordinary director, action, report, commitment, and communication records;
- explainable return summaries;
- deterministic replay;
- deterministic save restoration;
- protected deadline and authority enforcement;
- v7-to-v8 migration;
- generated-state JSON Schema and cross-reference validity.

## Not implemented

- real-time or asynchronous background work;
- scene rendering or player-visible return summaries;
- tactical combat or navigation simulation;
- unrestricted faction planning;
- procedural schedule authoring;
- report routes outside authored definitions;
- learned strategic authority;
- automatic wall-clock advancement.

## Next bounded slice

AI-10 remains an experimental tactical and learned-behavior boundary. Any learned policy must operate behind stable action interfaces and cannot bypass damage, authority, resources, or deterministic gameplay fallback.
