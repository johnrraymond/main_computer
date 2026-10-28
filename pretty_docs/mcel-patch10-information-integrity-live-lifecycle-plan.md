# MCEL Patch 10 — Information Fidelity and State-Preserving Live Lifecycle

**Current state: PARTIALLY VERIFIED implementation (Patch 10).** This document retains the original acceptance criteria; offline lifecycle evidence is green, hosted Windows acceptance remains BLOCKED until an actual served application is tested. See [Patch 10 Implementation Review](mcel-patch10-implementation-review.md).  
**Depends on:** [Patch 9 implementation review](mcel-patch9-implementation-review.md) and the bounded PASS for `mcel.semantic-collection-spec.v2`.  
**Authoritative requirements:** [Patch 10 User and Design Requirements](mcel-patch10-user-design-requirements.md).  
**Governing goals:** [MCEL Authoring Mission and Guiding Principles](mcel-authoring-principles.md).

> **Decision:** Prove correct, trustworthy, state-preserving delivery of *live* Task Manager information through MCEL's general semantic contract. Do not redesign Task Manager, transfer operational authority, build another demonstration application, or claim completion from offline fixtures.

## 1. User problem and precise evidence

The hosted Patch 8 Task Manager screenshot showed the same process data through a legacy interface and an MCEL process collection. MCEL displayed `0 B` where the legacy interface displayed `n/a`. The two presentations therefore disagree about whether memory was measured. Source analysis shows the legacy renderer prefers `memory_human`, while the existing v1 semantic contract projects `memory_rss`; **we must not assume either display is authoritative without examining the upstream telemetry producer**.

The currently tested tab workspace executes `destroy()` on every collection mount, clears the panel and remounts it during each `updateSnapshot`; preserving selected record, keyboard focus and inspection across refreshes is not established. The Task Manager refresh hook invokes the lab bridge's `publish()` without inspecting its result. Partial failure of the `all_processes` request is represented alongside a successful `processes` request, but the bridge presently accepts only a single snapshot-level status. Application restart was needed for the earlier browser to load patched scripts, so **served-build provenance** also needs a test.

Patch 9 established a bounded, domain-neutral information contract and derivation engine, but the live Task Manager contract remains v1; generic collection mounting was verified offline. Patch 10 must connect truthful *producer* information and a stable lifecycle to those mechanisms without corrupting or obscuring source data.

## 2. The experience we want

A person monitoring processes should see accurate values, including correct distinction between measured zero and unavailable measurement; be able to inspect the complete authoritative record; keep their selected process/details open during ordinary refreshes; and see clear loading/stale/partial/error indicators when live data is not trustworthy. Read-only monitoring must not change any process, schedule, or server setting.

An MCEL author should declare which upstream fields are measurements, how availability/freshness is established, and what uniquely identifies a changing record **once**. They must not author bespoke renderers, Task Manager-specific diff algorithms, process-specific framework handlers, or resize/refresh timing logic.

## 3. Normative boundaries

```text
Telemetry producer / original API (does it measure or omit a value?)
          | typed values, source metadata, separate stream status
          v
Application-owned semantic binding (source-specific names and authority)
          | general v2 contract; missingness is not guessed
          v
General MCEL value/provenance projection (Patch 9 machinery)
          | stable semantic identity, exact source record, field states
          v
General keyed collection reconciliation + versioned snapshot envelope
          | update only changed records; preserve selection, focus, inspection
          v
Existing registered elements and shared layout authority
          | opt-in, read-only monitoring; legacy remains fallback
          v
Hosted Task Manager observations + independent evidence ledger
```

- Domain-specific names and source interpretation **belong in producer/app bindings only**. The shared engine handles generic validity, source authority, identities, versions and reconciliation. No `pid`, `memory_rss` or Task Manager selector appears as a special condition in shared code.
- Missing, failed and stale are distinct, independently represented statuses. No metadata invented to turn a test green. If the producer cannot distinguish a zero from unavailable, preserve that uncertainty and record the needed producer fix.
- Keep the v1 normal/legacy route as it is until an explicit, independently verified promotion decision. Patch 10 targets the opt-in read-only route and generalized mechanisms. Existing operational authority remains untouched.

## 4. Implementation sequence

### 10.1 — Audit source truth and establish a telemetry baseline

Trace the actual `/api/applications/task/overview` producer and its serialization of `memory_rss`, `memory_human`, `status`, `created_at`, and process identity. Record whether memory zero is actually sampled; how `n/a` is produced; whether values and timestamps may be absent; and what stable process identity evidence exists beyond PID. Do not change telemetry producer semantics as a side effect of a formatter repair. Capture authoritative read-only snapshots and the legacy display for repeatable comparison; scrub machine-specific command lines from shareable evidence.

**Gate:** A source-backed **measurement validity truth table** exists, and real `0 B`/`n/a` mismatch cases have an explained cause or remain explicitly UNRESOLVED. Do not force agreement by hiding either discrepancy.

### 10.2 — Bind live information to the existing general v2 contract

Use an application-owned adapter/binding to supply the v2 availability, freshness, conflict and source identity metadata justified by step 10.1. Reuse the exact `McelSemanticValueContract`, generic semantic collection projector and shared layout authority from Patch 9. Avoid a new parallel validity language. Preserve exact original values; derived/display values cannot silently substitute for source values. Missing upstream evidence must remain marked unknown/unavailable rather than fabricated.

**Gate:** Correct displays and exact inspection for live measured-zero, unavailable, absent, explicit null, empty, stale, failed and contradictory cases *where authoritative evidence supports those states*; identical general contract works on parcel fixture without edits to shared code.

### 10.3 — Implement generic stable identity and incremental collection updates

Introduce a versioned, general `update`/reconcile contract for mounted semantic collections instead of wholesale destroy/recreate on every snapshot. Stable logical records preserve selected identity, open inspector, focus, and scroll where appropriate. Rendered data changes in place. Deletions have explicit policies: gracefully close or mark missing; never silently redirect the inspector to another item with a reused key. PID alone may be insufficient after process exit/reuse; require a verified composite identity when the producer makes it available, otherwise **refuse identity continuity across ambiguous reuse**.

**Gate:** Insert/update/delete/reorder and identical-refresh cases keep the expected DOM/interaction identity, with explicit failure on duplicate/ambiguous identities. A non-process collection demonstrates the same API and behavior.

### 10.4 — Make refresh delivery ordered, attributable and observable

Define a bounded, versioned snapshot/envelope with producer timestamp (when available), local observation revision, source identity, stream status, and delivery result. Enforce monotonic acceptance and refuse stale/out-of-order updates before committing them. Distinguish `processes`, `all_processes`, `connections` and `hardware` availability where supported: a failed all-process request must not make a successful primary process snapshot look globally healthy or globally failed. Retry and lifecycle transitions must be idempotent. The host must inspect failed `publish` results and record them in lab diagnostics, **not silently proceed as MCEL success**.

**Gate:** Controlled late responses, repeated revisions, partial-source failures, initial load, error/recovery and remount/unmount all produce correct visible state and a recorded receipt; no UI action escapes the read-only boundary.

### 10.5 — Test the running Task Manager, not merely fixtures

Provide a browser acceptance harness that reaches the actual hosted Task Manager route in explicit lab mode, confirms that the served script/contract fingerprint matches the installed build, waits for a real snapshot, and compares **identity sets and authoritative value states**, not just counts. Inspect the original record, select it, refresh, and verify selection/focus/scroll/inspector continuity. Exercise transient network/request errors and recovery without invoking destructive operations. On an unavailable server, mark `BLOCKED` or `UNVERIFIED`; an offline simulation is useful but is **not** a live-route pass. Require server restart/hard reload for verification after installation.

**Gate:** Live HTTP acceptance plus negative controls and actual report artifacts; no alternative path that marks the live barrier PASS solely on synthetic data.

### 10.6 — Regression, performance, and Promise Frob integration

Run the Patch 9 generality Frob, Patch 8 collection and guarded-lab tests, earlier layout Frobs, and ordinary Task Manager host regressions. Measure initial render and sustained-update latency/DOM churn/memory against the pre-patch baseline; freeze acceptable non-regression thresholds *before* final qualification. Document editor/File Manager/Website Builder remain untouched. Add separate `information-fidelity` and `state-preserving-live-lifecycle` barriers, with source-bound reports and an explicit live HTTP evidence flag. Recompute, do not read a cached green status.

**Gate:** No regression; no new application-specific framework branches; no operational effects; complete MCEL product promise continues to read `INCOMPLETE` unless independently supported by other evidence.

## 5. Acceptance and refusal principles

- **Measured zero** may display `0 B`. **Unavailable** may display `Unavailable`; a placeholder `n/a` never proves whether the numeric field is measured.
- A correct count with different process identities **fails parity**. Equal labels with divergent freshness or source meaning **fail fidelity**.
- A failed secondary feed must be independently visible; no global success flag can mask it.
- An identity reused after departure must not inherit the prior record's selection, inspector or operational target without verified continuity.
- An MCEL publication refusal must not be swallowed. The legacy monitor may remain functional; the experimental monitor must show the failure and the lab must report it.
- A safe, read-only test is not proof of the authorization of destructive actions; that remains a later patch.

## 6. What is explicitly NOT Patch 10

No wholesale application/workspace redesign, tab removal, aesthetic certification, full hierarchical shared-layout ownership, process terminate/restart/schedule/AI operational changes, Task Manager-specific framework reconciliation rules, new imaginary Inventory/Work Orders applications, or retroactive edits to historical benchmark evidence. Existing File Manager may later serve as a second real-app integration target only if a structurally distinct independent validation is needed. Leave Website Builder untouched.

## 7. Evidence and deliverables (proposed)

1. Source-provenance/validity audit and real telemetry truth table.
2. Generalized, backward-compatible v2 live binding and versioned snapshot semantics.
3. Generic keyed collection lifecycle/reconciliation mechanism with a non-process negative/control dataset.
4. Hosted Task Manager lab acceptance and observable error/recovery receipts.
5. Regressions and performance/DOM churn comparison.
6. New information-integrity and live-lifecycle Promise Frob barriers, docs and a manifest-free patch ZIP.

**Suggested CLI entry points (names proposed; implementation must ship them before documents claim they work):**

```powershell
python tools/mcel_live_semantic_lifecycle_frob.py --offline
python tools/mcel_live_semantic_lifecycle_frob.py --hosted http://127.0.0.1:8765
python tools/mcel_semantic_generality_frob.py --regressions
python tools/mcel_promise_gap_frob.py
```

The hosted command must genuinely reach the running server. Offline mode must never satisfy the hosted acceptance gate.

## 8. Go/no-go decision

**GO to Patch 11 only if:** author- and user-facing fidelity requirements pass on the live hosted Task Manager; generic keyed reconciliation works on unrelated semantic data; state/focus/identity survive refreshes; partial failures and refusals are visible; previous contracts and safety boundaries remain unchanged; and no outstanding critical source ambiguity is hidden or unexamined. Otherwise **NO-GO** with a precise unresolved barrier and corrective owner.

**The complete MCEL promise remains INCOMPLETE** until separately verified presentation quality, whole-workspace authority, governed operations, flexibility and authoring economy are proven. Patch 10 must not borrow those claims.
