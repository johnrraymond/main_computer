# MCEL Patch 13 — Completion: Presentation Compatibility and Authoring Economy

**Status:** Offline acceptance PASS; optional Windows hosted presentation check NOT RUN in this source export. **Production UI promotion: NO.**

## Purpose

Patch 11 taught MCEL native computer concepts. Patch 12 compiled them into the existing `mcel.application-ir.v1`. Patch 13 foundations made observation and identity semantics explicit, Stage 3 proved real live shadow adoption, and Stage 4 certified read-only hosted process continuity (on the owner's Windows host, independently of this completion cut).

The completion cut addresses two remaining questions:

1. Can canonical Task Manager observations supply **the same useful process information** as the existing `mcel-task-manager-process-contract.js`, without duplicating its definitions of kinds, data sources, validity, identity, or role computation?
2. Does this actually reduce application-view authoring decisions, including any presentation overlay introduced for compatibility?

**It does not claim byte-for-byte or pixel-for-pixel equivalence, unhinted application construction, or production promotion.**

## Changes

| Element | Responsibility |
| --- | --- |
| `main_computer/mcel_knowledge/catalog/00-computer-core.json` | Declares command-line concept's raw representation as an authoritative string. It is a semantic representation fact, not a display rule. |
| `main_computer/mcel_knowledge/ir_bridge.py` | Projects catalog `derivedProperties` as typed v2 fields. A semantic derivation exists once; a consumer does not re-declare it. Supports string-represented structured values as `long-text`, while other structured values remain exact-inspector-only. |
| `main_computer/mcel_knowledge/presentation_policy.py` | Generic, validated, opt-in compatibility overlay over the existing v2 projection. Does not modify canonical records, identities, reconciliation proofs, source formats, or authority. |
| `main_computer/mcel_knowledge/presentation/task-manager-processes.v1.json` | Small app-owned compatibility overlay: selected fields, labels, primary/detail exceptions, command abbreviation, and role display labels. It is **counted as author work**. |
| `main_computer/mcel_knowledge/live_adoption.py` | Optional `presentation_policy=` argument to the existing read-only same-response resolver; absent in the default path. |
| `main_computer/viewport_routes_task.py`, `main_computer/web/applications/scripts/terminal.js`, `main_computer/web/applications/scripts/mcel-task-manager-semantic-shadow.js` | An additional explicit `compat` switch **inside** the existing read-only shadow lab; ordinary Task Manager requests are unchanged. |
| `main_computer/mcel_knowledge/authoring_audit.py` | Frozen-baseline declaration audit, failing closed if the legacy contract changes. |
| `tests/test_mcel_patch13_completion.py` | Source fidelity, role derivation, safe identity, layout metadata, refusal, and audit tests. |
| `tools/mcel_patch13_completion_frob.py` | Real v2 browser tests at 320, 390, 560, and 1100 pixels, plus optional localhost hosted source/browser test. |

### Semantic / presentation boundary

The native catalog tells MCEL that `resident_memory` is a byte measurement with availability, `created_at` is a timestamp, process identity includes PID and normalized creation time, and `role` is an evidence-marked application semantic derivation. The compatibility overlay does **not** restate any of those.

For example, `role` is calculated once by the native semantic interpreter into `current-process`, `main-computer`, or `other-process`. The overlay only maps those values to the existing Task Manager captions *Current*, *Main Computer*, and *Other*. The full canonical observation still records the original semantic role and derivation evidence.

The command field remains a full, exact source value in `mcelCanonicalRecord` and the inspector. The existing v2 display can abbreviate its presentation to the legacy 46-character limit without changing the source value.

### Existing presentation compatibility

The existing v2 collection consumer displays the legacy Task Manager's seven information categories, order, labels, tiers and field kinds:

| Semantic property | Display label | Tier | Kind |
| --- | --- | --- | --- |
| pid | PID | primary | identifier |
| name | Process | primary | text |
| role (derived) | Role | secondary | enum (display vocabulary only) |
| resident_memory | Memory | secondary | quantity (bytes) |
| state | Status | secondary | enum |
| command | Command | secondary | long-text |
| created_at | Started | detail | timestamp |

The canonical v2 consumer **also renders `mcelIdentity` as an extra primary field**. That is intentional: it carries host+process-lifetime identity. Changing it to PID for superficial parity would weaken the reconciliation contract. The Frob records this visible difference. **Pixel equivalence is not claimed.**

The overlay does not install an application's new CSS or layout logic. It works with the existing generic v2 consumer at four viewport widths, without horizontal overflow in the offline tests.

The new opt-in live view is at:

```text
http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab&mcel_semantic_shadow=lab&mcel_semantic_presentation=compat
```

The route will apply the presentation overlay only when both the semantic shadow is requested and `mcel_semantic_presentation` is exactly `compat`. Other flags do not create effects or change normal Task Manager rendering.

## Authoring-economy baseline — measured, narrow

`python -m tools.mcel_patch13_completion_frob` audits the actual checked-in 25-line Task Manager process contract, rather than a hypothetical baseline.

| Declaration type | Existing handwritten view | New app-owned overlay |
| --- | ---: | ---: |
| Source field names | 10 | 0 |
| Domain kind annotations | 7 | 0 |
| Field source mappings | 6 | 0 |
| Availability links | 2 | 0 |
| Generation links | 1 | 0 |
| Role derivation rule + branches | 3 | 0 |
| Byte-unit declaration | 1 | 0 |
| Explicit priorities | 4 | 0 |
| Field selection / declarations | 7 | 7 |
| Display labels | 7 | 7 |
| Tiers / explicit tier exceptions | 7 | 3 |
| Disclosure choices | 2 | 2 |
| Display value labels | 0 | 3 |
| **Total counted explicit declaration instances** | **57** | **22** |

These are counts of **view-specific authored declarations**, not lines of production code or person-hours. Some savings are only achievable because Patch 11–13 already invested in shared catalog, interpreter, compiler, tests and adapters; that cost is real and is not included in this narrow measurement. An overlay still exists, and the exact hosted production GUI has **not** been replaced. No measured authoring savings are claimed for File Explorer or Worker, since neither has a comparable handwritten v2 process contract baseline.

The unhinted default path remains available with `presentation_policy=None`, but it does **not yet** produce equivalent view choices by itself. Full unhinted application generation is a separate experiment.

## Acceptance and limitations

Run on local development checkout:

```powershell
python -m tools.mcel_patch13_completion_frob
```

Optional hosted Windows check, with the server already running:

```powershell
python -m tools.mcel_patch13_completion_frob `
  --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab"
```

The hosted option samples the real Task Manager `/api/applications/task/overview` with `mcel_semantic_presentation=compat`, verifies that the **served** shadow includes the explicit overlay, then opens the live opt-in lab and injects a second **test-only** v2 collection into the browser page. It never replaces the existing application UI. It tests the served data and generic consumer but is not a production adoption or exact visual parity certificate. The source snapshot is compared against its own semantic resolution, not against a second volatile OS process sampling.

Regression command:

```powershell
python -m pytest -q `
  tests/test_mcel_application_ir.py `
  tests/test_mcel_patch13_foundations.py `
  tests/test_mcel_computer_knowledge.py `
  tests/test_mcel_computer_knowledge_multiapp.py `
  tests/test_mcel_knowledge_ir_bridge.py `
  tests/test_mcel_patch13_live_shadow.py `
  tests/test_mcel_patch13_continuity.py `
  tests/test_mcel_patch13_hosted_source_reliability.py `
  tests/test_mcel_patch13_completion.py
```

In the supplied export, 124 tests and 14 subtests pass. Full viewport-route import is blocked in this export by the missing `game_projects/webgl-demo/game.json` fixture; the hosted completion command on the actual Windows server provides that independent route check. Stage 3 and 4 offline acceptance Frobs also pass. Hosted completion Frob: **NOT RUN** from this container. Earlier Windows-hosted Stage 4 continuity PASS was reported separately by the user and is not re-certified by this patch.

**Status of MCEL Promise:** semantic reuse and view-authoring economy now have concrete evidence. General automatic layout, independent aesthetic assessment, full unhinted flexibility, and governed application-effect proof remain separate, unverified Promise Gap gates. No blanket claim that the MCEL promise is complete.

**Invariants:** No new IR schema, no new layout optimizer, no website-builder changes, no app controls modified, no operational execution, no automatic production promotion, and no weakening of source-evidenced process-generation continuity.
