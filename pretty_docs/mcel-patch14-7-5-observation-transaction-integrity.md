# MCEL Patch 14.7.5 — Observation and Transaction Integrity

Read-only experimental Task Manager semantic collection. **Not production promotion.** Requires 14.7.4.

## Observation integrity

`McelObservationIntegrity.evaluate(previousMap, incomingMap, observation, cursor)` is a pure, domain-independent source-membership planner.

`observation` may be omitted by legacy unversioned callers; those retain their previous **complete snapshot** semantics until a versioned observation has been accepted. Explicit observations have:

```js
{
  schema: 'mcel.source-observation.v1',
  sequence: 17,       // safe nonnegative integer, strictly increasing
  complete: false,   // true = entire scoped membership; false = partial observation
  removals: ['key']  // optional authoritative explicit removals (not present in incoming)
}
```

- Complete snapshot: missing IDs are authoritative absences.
- Partial snapshot: omitted IDs remain in the accepted source; only explicitly listed `removals` establish absence.
- Older or repeated sequences are refused, without updating source state, layout, or retirement.
- Conflicting remove-and-observe inputs, malformed observations, oversize merged sources, and unversioned updates after sequenced mode are refused.
- The sequence cursor advances **only after** the source update and layout installation succeed. Refused updates cannot consume a sequence.
- Current scope is the supplied collection. No cross-source epoch reconciliation, remote transaction guarantee, or production observer transport is implied.

Example (opted-in lab):

```js
lab.update(nextPlan, {observation: {
  schema: 'mcel.source-observation.v1', sequence: 17,
  complete: false, removals: []
}});
```

## Presentation transactions

`McelPresentationNegotiation.createTransaction()` returns an ordered undo registry (`addUndo`, `commit`, `rollback`). A host can supply `context.beginTransaction` to `negotiate`. The coordinator creates the transaction **before** execution and rolls it back if the executor returns failure or throws. If rollback itself fails, negotiation refuses further fallbacks (`rollback-failed`). An executor must register each reversible side effect before executing it; external effects cannot be made reversible by this interface.

The opted-in collection's inline expansion and floating-card attempts now use this transactional path. Existing defensive inline cleanup remains in place. Legacy callers that do not provide `beginTransaction` retain their old contract and are **not** guaranteed rollback on exception.

## Selection/layout boundary

Native `selectionchange` may be asynchronous. A row reorder can synchronously invalidate the selection that justified a deferred display value. Before layout verification, MCEL now synchronously rechecks pending selected fields and updates them when no native selection protects them. The canonical validator remains strict and does **not** accept an arbitrary stale-value flag.

## Stateful adversarial Frob

```
python -m tools.mcel_patch14_7_5_adversarial_lifecycle_frob
```

The Frob runs real Task Manager semantic fixtures in Chromium at 390 and 1440 px, with normal and stressed data. Three deterministic seeds per configuration each execute 55 event steps. Events include complete and partial snapshots, explicit removals, verified-ID reappearances, stale and duplicate versions, native text selection, selection release, inline expansion, hover overlays, and user scrolling. After each step it checks membership, accepted field values, DOM/retained membership, and canonical layout validity. It also injects an exception *after* a presentation mutation and checks rollback-before-fallback and fail-closed rollback failure.

Report: `runtime/reports/mcel-patch14-7-5-adversarial-lifecycle/report.json` (full event trace for reproduction). Tests are deterministic **sequential interleavings**, not actual multithreaded races. No virtualized list adapter, sleep/hidden-tab timing, arbitrary cross-document text selection, security revocation, or production live route is certified here.

Existing retirement timer policies and element-owned fallback semantics are unchanged.
