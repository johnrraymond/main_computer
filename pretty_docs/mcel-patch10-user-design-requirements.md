# MCEL Patch 10 — User and Design Requirements / Acceptance Scorecard

**State: PATCH 10 PARTIALLY VERIFIED.** Requirements retain their preimplementation acceptance criteria. Independent browser/producer evidence is green; hosted-route gates T02/T03/T12 remain BLOCKED/UNVERIFIED until run against the user's actual application server.  
**Companion:** [Patch 10 Plan](mcel-patch10-information-integrity-live-lifecycle-plan.md).  
**Prerequisite:** [Patch 9 Verified Feature Review](mcel-patch9-implementation-review.md) — bounded domain-neutral v2 semantics, not yet deployed in live Task Manager.  
**Guiding authority:** [MCEL Authoring Mission and Guiding Principles](mcel-authoring-principles.md).

> **Review question:** Does the *actual hosted Task Manager* display only defensible information states, keep the same logical record and user context through refreshes, and report failed or partial updates—using generic MCEL machinery shared with a non-process dataset?

## 1. User requirements — observable outcomes

| ID | User requirement | What an independent observer must see |
| --- | --- | --- |
| **M10-U01** | Truthful live measurements | Measured zero differs from unavailable/not measured. Live `0 B` vs legacy `n/a` discrepancy is resolved from the producer or retained as an explicit uncertainty; never cosmetically suppressed. |
| **M10-U02** | Complete, inspectable data | Every visible value has recoverable authoritative source and status; full command lines/Unicode/identifiers survive display compression. |
| **M10-U03** | Reliable first load | After a fresh server restart/browser load in explicit lab mode, the MCEL view populates from the current snapshot without console intervention. |
| **M10-U04** | Refresh does not destroy user context | For continuing records, selected row, open exact inspector, focus and relevant scroll position remain stable under repeated updates. |
| **M10-U05** | Clear information freshness and partial failures | Loading, empty, available, stale, failed and partially available sources are distinguishable; success of one feed cannot conceal another feed's failure. |
| **M10-U06** | Correct record identity | Process identity sets match the same bounded authoritative snapshot. Reused or ambiguous identities never inherit stale details. |
| **M10-U07** | No accidental operations | Monitoring, refresh, inspecting and tab selection cause zero terminate/restart/scheduling/AI or other consequential actions. |
| **M10-U08** | Usable diagnostics and recovery | Lab exposes publication refusals and served build/snapshot revisions, and recovers from temporary failure without silently reporting a PASS. |
| **M10-U09** | No regression or special-case authoring | Original v1 path and previous Frobs still pass; unrelated datasets use the same update/value engine without renderer-specific code. |

## 2. General design requirements

| ID | Architectural contract | Required proof |
| --- | --- | --- |
| **M10-D01** | Producer owns telemetry truth; app contract binds source meaning; shared MCEL contains no process-field special cases | Producer trace + source audit + renamed-field metamorphic test |
| **M10-D02** | Reuse `mcel.semantic-collection-spec.v2` rather than creating a competing value authority | Same v2 validator/projector/render/layout entry points on process and non-process data |
| **M10-D03** | Availability, freshness, conflict and failure are independent typed dimensions with provenance | State matrix including known zero, source omission, explicit null/empty, stale known, failed read, conflict |
| **M10-D04** | Original source values are immutable; presentation strings are never authoritative | Exact-value comparison before and after projection/formatting/refresh |
| **M10-D05** | Semantic identities distinguish continuous records from deleted/reused identities | Stable keys plus producer-supported generation/creation evidence, or explicit refusal when continuity is ambiguous |
| **M10-D06** | Generic incremental reconciliation replaces unconditional destructive remounts where records continue | DOM/row continuity, inspector/focus/scroll assertions on insert/update/delete/reorder/no-op |
| **M10-D07** | Versioned ordered snapshots and safe stale/late-response refusal | Revision/freshness checks and adversarial out-of-order responses without rollback of visible data |
| **M10-D08** | Per-source status and partial success travel with the snapshot | Simulated primary-success/secondary-failure and recovery cases remain separately visible |
| **M10-D09** | All host publication outcomes are observed with actionable diagnostics | Refusal test makes lab status non-successful while legacy route remains functional |
| **M10-D10** | Hosted-route acceptance identifies actual served code and live source response | Browser network/host receipt, build fingerprint, real snapshot evidence; blocked server is NOT PASS |
| **M10-D11** | Strict read-only authority and reversible lab adoption | No unexpected requests/actions, exact unmount/fallback, production route unchanged |
| **M10-D12** | Compatibility and bounded resource use | Prior suites pass; sustained refresh, DOM churn, latency/memory reviewed versus frozen baseline |
| **M10-D13** | Independent evidence recorded and recomputed in Promise Frob | Source hashes, timestamps, precise scope, negative controls and refusal reason retained |
| **M10-D14** | Minimal authoring, not a bespoke Task Manager fix | Count new semantic declarations/adapters/CSS/decisions and verify no non-process consumer modifications needed |

## 3. Test matrix and expected negative controls

| Test ID | Positive experiment | Failure/negative control | Primary requirements |
| --- | --- | --- | --- |
| **M10-T01** | Trace real producer memory/status fields; populate live validity contract | Legitimate measured zero vs unavailable zero; conflicting `memory_human`/`memory_rss` cannot be papered over | U01; D01–D04 |
| **M10-T02** | Exact live snapshot identity and values match MCEL | Equal row count but deliberately different IDs fails | U02, U06; D04–D05 |
| **M10-T03** | Cold server startup, hard reload, auto-initial display | Old script fingerprint or missing consumer refuses acceptance | U03, U08; D09–D10 |
| **M10-T04** | Repeated no-op and changed snapshots retain user inspection | DOM remount, lost focus/scroll, wrong inspector ID fails | U04; D05–D06 |
| **M10-T05** | Process add/remove/reorder; duplicate ID and PID-reuse simulation | Stale selection or transferred details fails closed | U04, U06; D05–D07 |
| **M10-T06** | Two out-of-order live responses | Late stale response must not overwrite newer view | U05, U08; D07–D09 |
| **M10-T07** | Successful processes request and failed all-processes request | Global 'available' without per-source failure is rejected | U05; D03, D08–D09 |
| **M10-T08** | Simulate publication refusal followed by valid recovery | Silent ignored `publish` result fails | U08; D09, D13 |
| **M10-T09** | Same lifecycle API on parcel fixture with renamed fields | Engine source edits, process-only branches or second reconciler fail | U09; D01–D02, D06, D14 |
| **M10-T10** | Lab monitoring and rollback remain read-only | Any destructive endpoint, effect or changed production route fails | U07, U09; D11 |
| **M10-T11** | Prior Patch 9, Patch 8, layout and host tests; measured sustained refresh | Regression or significant new churn/latency without accepted reason fails | U09; D12 |
| **M10-T12** | Fresh Frob evidence for offline and actual HTTP modes | Missing server, stale report or synthetic-only pass cannot satisfy live gate | U03, U08; D10, D13 |

**Do not weaken a negative control to produce green output.** Define performance budgets from an actual baseline before release qualification, and record exact values rather than inserting ungrounded thresholds here.

## 4. Source-backed ambiguity rules

- `memory_rss=0` plus `memory_human='n/a'` is an **inconsistency requiring producer analysis**. Neither field unilaterally proves truth; use observed source semantics. Where unknown, present `Unavailable`/`Unknown` with provenance rather than asserting measurement.
- A `present` value can also be stale or conflicting. An `unavailable` value is not a numeric zero. An empty array from a failed source is not verified evidence of *no processes*.
- Do not infer PID continuity across time from numeric equality alone. Validate an upstream stable generation/creation marker when available; otherwise avoid falsely persisting selection across possible reuse.
- Preserve the user's active context only where it is safe. A disappearing/ambiguous record should receive an explicit explanation, not silently shift to another target.
- A failed HTTP/backend response and a failure to publish into the MCEL renderer are independent facts. Report both where they occur.

## 5. Review worksheet (all states at creation)

| Evidence area | Status | Required acceptance |
| --- | --- | --- |
| Producer telemetry meaning | PROPOSED / UNVERIFIED | T01 |
| Live v2 semantic interpretation | PROPOSED / UNVERIFIED | T01–T03 |
| Stable keyed update and focus/selection | PROPOSED / UNVERIFIED | T04–T05, T09 |
| Ordered revision and partial failure | PROPOSED / UNVERIFIED | T06–T08 |
| Actual hosted-route acceptance | PROPOSED / UNVERIFIED | T02–T03, T12 |
| No operations + rollback | PROPOSED / UNVERIFIED | T10 |
| Backward compatibility and performance | PROPOSED / UNVERIFIED | T11 |
| Domain neutrality and author burden | PROPOSED / UNVERIFIED | T09, T11 |
| **Patch 10 overall** | **NOT IMPLEMENTED / NOT VERIFIED** | All mandatory gates with admissible evidence |
| **Complete MCEL product promise** | **INCOMPLETE** | Later default-design, full composition, effects and economy gates |

For every result keep `requirement_id`, `status` (`PASS`, `FAIL`, `BLOCKED`, `UNVERIFIED`, `REGRESSION`), `test_id`, producer/source/build fingerprint, sample identity set or privacy-safe digest, expected/observed outcomes, browser/server context, report path and timestamp. No report may claim success on a hosted route that was not actually reached.

## 6. MCEL guiding-principle traceability

| Guiding principle | Patch 10 contribution | Outside Patch 10 |
| --- | --- | --- |
| **Minimal authoring effort** | General validity/source bindings and generic reconciler, no hand-wired per-app refresh plumbing | Equivalently correct authoring-time benchmark |
| **Attractive results by default** | Stable, honest data states and uninterrupted interaction | Visual polish, density, full hierarchy certification |
| **Maximum sound flexibility** | Contract-driven update and partial-source policies; independent domain transfer | Free-form design systems, alternate whole-workspace layouts |
| **Correctness through composition** | Typed source semantics, identity/revision/refusal boundaries, live-source/renderer receipts | Consequential operational authorization and full application action parity |

**Stop/go:** No Patch 11 presentation redesign until live source semantics, stable lifecycle and monitored refusal/recovery gates pass in the real hosted Task Manager. Preserve Website Builder and historical benchmarks unchanged.
