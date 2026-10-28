# MCEL Patch 10 — Implementation and Requirements Review

**Status: PARTIALLY VERIFIED — not a hosted-route certification and not full MCEL promise completion.**

Authoritative baseline: [Patch 10 User/Design Requirements](mcel-patch10-user-design-requirements.md) and [Plan](mcel-patch10-information-integrity-live-lifecycle-plan.md). Architecture prerequisite: [Patch 9 Review](mcel-patch9-implementation-review.md).

## Delivered functionality

- The Task Manager Windows PowerShell producer had emitted `memory_rss=0` as a placeholder alongside `memory_human='n/a'`, while its query never measured memory. The revised producer uses null RSS with explicit `memory_availability='unavailable'`. The psutil path produces measured RSS with explicit availability; unknown status is labelled unavailable. No operational methods or actions changed.
- The app-owned process contract adopts `mcel.semantic-collection-spec.v2` and declares `created_at` as its reconciliation generation source. Shared code contains **no Task Manager field names or process-specific reconciliation code**.
- The general collection consumer supplies a keyed `update(rows)` operation. Rows with validated identical record-generation evidence keep DOM identity, keyboard focus, and current exact inspector; changed/unknown generation drops unsafe continuity. Original data is copied for exact inspection. Duplicates are rejected before update.
- The generic tab workspace reuses mounted semantic collections, preserves successful data during per-source loading/error/stale periods, and reports per-source receipts. Older generic `updateSnapshot` response shape is retained when no semantic collection or stream metadata is used.
- The lab bridge validates ordered revisions, refuses stale or failed renderer updates, records the last 32 receipts, exposes a build marker, and represents partial stream failures without reporting global healthy availability. Task Manager host delivery refusals are observable independently of successful legacy refresh.
- A dedicated offline Chromium acceptance exercises read-only lifecycle and an unrelated parcel schema at four widths. An optional hosted browser test verifies cold load, served build, exact identity-set parity, and a second actual API refresh. No simulated response can turn its hosted gate green.

## Evidence and requirement verdicts

| Requirements | Verdict | Evidence / limit |
| --- | --- | --- |
| M10-U01, D01–D04, T01 | **PASS, producer-trace and offline** | Windows PowerShell producer mock verifies unmeasured RSS is null/unavailable; browser compares known zero to unavailable zero and exact source inspection. Actual Windows endpoint values still require live verification. |
| M10-U02, D04, T02 | **PARTIAL_VERIFIED** | Exact record inspection and immutable test snapshots; hosted identity and value parity is BLOCKED without a running server. |
| M10-U03, D10, T03 | **BLOCKED** | Runtime has a build marker and a hosted check, but no user-hosted HTTP session was reached from this container. |
| M10-U04/U06, D05–D06, T04/T05 | **PASS, offline browser** | Same-generation row persistence, focused selected row, inspector refresh, insertion/deletion/reorder, duplicate refusal, PID-generation replacement. Hosted repetition is pending. |
| M10-U05/U08, D07–D09, T06–T08 | **PASS, offline browser** | Ordered revision refusal, failed secondary source retained and flagged, receipt + refusal recording. Out-of-order HTTP races remain a separate host requirement. |
| M10-U07, D11, T10 | **PASS, guarded offline** | Zero network/effect requests in lab Frob; legacy controls unchanged and exact lab rollback. Full operational authority is not certified. |
| M10-U09, D01/D14, T09 | **PASS, bounded** | Renamed parcel schema uses exact same shared consumer and update API. No application/domain-specific branches added to shared code. |
| M10-D12/T11 | **PASS for functional regressions; performance UNVERIFIED** | Patch 8 notebook and semantic, Patch 9 generality, and previous representation/layout Frobs pass. No independently frozen pre/post performance budget or sustained host-memory benchmark. |
| M10-D13/T12 | **PARTIAL_VERIFIED** | Promise Frob recomputes offline results, keeps hosted flag BLOCKED; explicit URL required for actual hosted evidence. |

**No claim of full Patch 10 certification** until hosted-mode tests, real telemetry provenance, sustained refresh performance and remaining criteria have admissible evidence. No advancement to Patch 11 should be inferred merely from these offline PASSes.

## Commands

```powershell
python -m pytest tests/test_mcel_task_manager_patch10_producer.py -q
python -m tools.mcel_task_manager_live_lifecycle_frob
python tools/mcel_semantic_generality_frob.py
python -m tools.mcel_task_manager_semantic_pipeline_acceptance
python -m tools.mcel_task_manager_notebook_lab_acceptance
python tools/mcel_representation_layout_frob.py
python tools/mcel_promise_gap_frob.py
```

After **restarting** the Windows app process to activate changed served JavaScript, verify the genuine host:

```powershell
python -m tools.mcel_task_manager_live_lifecycle_frob --host-url 'http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab'
python tools/mcel_promise_gap_frob.py --task-manager-host-url 'http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab'
```

The opt-in URL is read-only; no process kill, restart, schedule, or AI operation is invoked by the test. If unavailable, results stay **BLOCKED**. The test's comparison uses identity sets and an additional actual refresh rather than counts alone.

## Architectural and user-facing remaining risks

- On Windows, `CreationDate` is supplied by CIM; its serialisation/stability must be checked against the hosted application. When absent/ambiguous, the reconciler deliberately declines identity continuity instead of guessing.
- `mcel.semantic-collection-spec.v2` is not yet unified with every authoring DSL/IR path (Patch 9 limitation persists).
- The generic non-semantic views retain existing table rebuild behavior; the keyed update pertains to declared semantic collections only.
- Task Manager's experimental second notebook remains intentionally duplicated with legacy; no presentation redesign or operational authority transfer occurs here.
- Full human accessibility/design assessment and equivalently correct authoring-economy benchmark remain later promise barriers.

**Review decision:** General semantic-to-live-lifecycle integration is demonstrably functional under independent offline tests. Hosted product acceptance and sustained performance measurement remain mandatory next steps, and the MCEL promise remains INCOMPLETE.
