# MCEL Patch 9 — Architecture Audit and Requirements Review

**Review state:** BOUNDED ARCHITECTURAL PASS; **overall MCEL promise remains INCOMPLETE**.  
**Design authority:** [Patch 9 User and Design Requirements](mcel-patch9-user-design-requirements.md) and [Patch 9 Plan](mcel-patch9-semantic-architecture-plan.md).  
**Basis:** The release candidate's source and executable generality/regression Frobs; *not* the live Windows application server.

## 1. Decision

Patch 9 adds **`mcel.semantic-collection-spec.v2`** to the *existing semantic collection contract family*. It does **not** introduce a new authoring DSL or silently convert existing v1 Task Manager declarations. Both process and parcel examples use the exact same value interpreter, collection projector, registered components, and shared layout authority. The original v1 path remains the default for existing consumers.

**The verified claim is deliberately narrow:** a read-only, typed, provenance-bearing semantic projection can be reused across domains without editing shared runtime code. Patch 9 does not establish beauty, complete MCEL DSL/IR unification, a production application composition system, live telemetry accuracy, or authoring-cost savings.

## 2. Responsibility and authority audit (9.1)

| Source | Responsibility | Domain-specific logic allowed? | Patch 9 disposition |
| --- | --- | --- | --- |
| `mcel-task-manager-process-contract.js` | Task Manager's own field names, role precedence, source meanings | **Yes**, declarations only | Kept unchanged on v1; no hidden migration |
| `mcel-semantic-value-contract.js` | Validate bounded v2 fields, interpret availability/freshness/conflict and provenance, derive general values | **No** | New pure shared interpreter; known operators: `first-true`, `coalesce`, `concat` |
| `mcel-semantic-collection-workspace.js` | Project records, select registered components, provide exact raw inspection | **No** | Additive v2 branch, explicit v1 compatibility, no field-name recognition |
| `mcel-semantic-layout-inference.js` | Generic width allocation and disclosure | **No** | Reused unchanged from Patch 8; v2 supplies normalized field contracts |
| `mcel-element-registry.js` and `mcel-elements-core.js` | Registered element identity and rendering | **No** | Reused; no domain-specific components added |
| `mcel-tab-workspace.js` | Generic tab orchestration | **No** | Unchanged; existing Task Manager lab path preserved |
| `mcel-task-manager-notebook-bridge.js` / `task-manager.js` | Live Task Manager opt-in, snapshot delivery and guarded lifecycle | **Host-specific only** | Unchanged; no effect or lifecycle authority moved |
| `applications.html` | Explicit script availability/order | **No** | Loads interpreter before consumer; no default production activation |

**Remaining architectural questions:** v2 is an additive family member, not yet a universal MCEL IR type. The current general consumer remounts for a new snapshot. Keyed incremental reconciliation, complete application composition, and the equivalently-correct authoring baseline belong to subsequent patches, not to claims about Patch 9.

## 3. User and design requirements: current evidence

| Requirements | Verdict | Evidence / boundary |
| --- | --- | --- |
| U01, D02 — cross-domain substitution, no domain branches | **PASS (bounded)** | Same shared source in Node and registered Chromium consumer; arbitrary physical field renaming and reordering |
| U03, D03–D04, D06 — validity, freshness and conflict; no zero/unknown collapse | **PASS (tested states)** | Zero vs unavailable zero, missing/null/empty/failed, present+stale, present+conflict, first-true unknown precedence |
| U04, D03 — full value, identity and provenance | **PASS (tested values)** | Exact inspector values, original source snapshots, Unicode long text, near-collision identities; no source mutation |
| U05, D05 — lawful bounded derivation/refusal | **PASS (tested operators)** | Wrong types, unknown dependencies, cycles, unsupported executable operator, invalid status/identity, duplicate IDs refused |
| U06, D07 — public tiers and same shared layout authority | **PASS (bounded)** | Primary/secondary/detail variant alters visible/disclosed fields in Chromium at 320/390/560/1100 px |
| U07, D08 — existing v1 behavior and regression stability | **PASS (tested suites)** | Explicit v1 projection probe; prior semantic pipeline, guarded notebook and representation-layout Frobs pass |
| D09 — no semantic-effect escalation | **PASS (bounded)** | Pure Node evaluation and offline Chromium, no Task Manager operations or HTTP requests |
| U08, D10–D11 — honest evidence and source-bound gate | **PASS (bounded)** | Frob executes fresh cases, hashes interpreter/consumer/fixtures and records failure results; Promise Frob recomputes gate |
| D01 — one **universal** MCEL IR/DSL authority | **PARTIAL** | v2 extends existing semantic collection family, but is not yet integrated into every MCEL authoring path |
| U02, D12 — reduced work versus equally correct alternative | **UNVERIFIED** | Counts available: 7 semantic fields and 3 validity bindings per fixture, no domain renderer helpers; no equivalent baseline or measured end-to-end author effort |
| Full Task Manager application design/operations | **NOT CERTIFIED** | Live route, presentation quality, incremental lifecycle, and governed effects remain outside Patch 9 |

A `PASS (bounded)` is not a product-wide certification. Tests do **not** establish all possible values or data shapes. Unverified claims must remain unverified.

## 4. Execute the release gates

From the repository root, with the app's Python and Node/Chromium dependencies installed:

```powershell
python tools/mcel_semantic_generality_frob.py --regressions
python tools/mcel_promise_gap_frob.py
```

The first writes `runtime/reports/mcel-semantic-generality/{report.json,review.md}`. The second writes a Promise Frob barrier report and recomputes the generality test; it must remain **INCOMPLETE**, not falsely declare the entire MCEL promise fulfilled. The generality Frob is expected to report `PASS` after a fresh installation and execution. On systems without a Chromium/Playwright installation, browser evidence is not certified.

The acceptance matrix is in the companion requirements document. Browser tests use **offline actual registered components and layout**, not a live HTTP route. Windows users should restart the application before evaluating changes to browser scripts; previous Patch 8 investigation established why an older process can keep serving older JavaScript.

## 5. Risks that still block broader claims

- **Information-producer semantics:** A real `memory_rss=0` marked `n/a` by the legacy UI may reflect unknown measurement; the new contract must be authored with the correct source validity facts. This patch does **not** relabel live telemetry.
- **Universal authoring surface:** Future DSL and compiler work must reuse these concepts without creating an independent semantic authority.
- **Live lifecycle:** No keyed incremental update, freshness timeout policy, or operational authorization is certified here.
- **Layout/design:** Measurable layout handoff is not proof that the generated UI is attractive or sufficiently information-dense.
- **Authoring economy:** Fewer domain renderer helpers do not prove less total work until matched to an equally correct baseline.

**Decision for Patch 10:** Only continue with a domain-neutral information-validity and live-refresh integration that preserves the v1 operational boundary and uses independent live-host parity tests. Do not commission another application or touch Website Builder.
