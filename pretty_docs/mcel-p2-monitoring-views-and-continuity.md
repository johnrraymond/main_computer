# MCEL P2 — Four monitoring views, semantic mode visibility and continuity

**Base:** MCEL P1 generated-composition patch against `main_computer_test-20261031-222255.zip`. This change is cumulative with P1; it does not alter production Task Manager routes or authorize process/server/schedule mutations.

## Scope and implemented contracts

- The official JavaScript DSL accepts optional `when: {state, equals}` for `surface.form`, `surface.collection`, and `surface.text`. The state must be a declared renderer-local string enum, and the value must be admitted by that enum. This declares which semantic *mode* owns the data; it is not a placement instruction or effect.
- The pure Python synthesizer accepts these conditions only when a verified local `viewTransition` targets the same state. It continues to derive one immutable whole-app composition from canonical IR and purpose. No app-name selection or authored HTML.
- The generated candidate now declares four distinct views: server processes, all processes, connections, and CPU/GPU. Each has a separate bounded, read-only capability method and result schema. Switching views is a local-state transition and never triggers an implicit fetch.
- The generated browser runtime hides only mode-inapplicable nodes and collapses structurally empty regions; shared `McelSemanticLayoutInference` recalculates feasible visible-region geometry. The canonical composition fingerprint remains unchanged across view switches, refreshes, and resizes.
- Collections are reconciled by stable record keys. Inline details, selected identity, DOM row identity, focus, and the first visible scroll anchor survive refresh/reordering. Selected-record details are computed from the authorized observed collection rather than fabricated from the selected key. Removed selections are explicitly marked stale.
- A generic typed-record presentation shows summary and typed scalar readings while placing high-volume telemetry arrays/raw data in accessible disclosure sections. Measurements are not truncated or silently reinterpreted. Expansion state survives a value refresh.
- The candidate's only backend endpoint is `/api/applications/task/overview` through its existing exact-scoped read port. No automatic polling and no AI model requests.

## Evidence and limitations

Run `python -m tools.mcel_p2_monitoring_frob`. This gate runs P1/P2 controlled Chromium suites and the existing Phase 2B runtime regression suite. It also feeds an authentic local OS snapshot from `TaskManagerService` into the browser through a **controlled transport adapter**: it verifies real PID/creation-time identity and typed GPU/CPU response handling, but **does not** test the production HTTP route or the user's Windows host. Browser localhost navigation is blocked in the available execution environment.

The existing production Task Manager still owns consequential operations, scheduling and AI briefs. These are explicitly **not** migrated into the generated P2 read-only candidate, so **full production parity and promotion remain blocked**. Human design acceptance and live Windows accessibility are unverified. P2 establishes the monitoring view mechanics and reusable continuity, not a complete replacement or a general-profile discovery certificate.

## Invariants for independent review

1. Search `main_computer/mcel_knowledge/semantic_composition.py` and generated runtime for application-name/process-specific branches: none may determine presentation.
2. Mutation of a `when` enum value or deletion of its legitimate `viewTransition` must fail closed before UI construction.
3. No hidden sibling HTML, authored structural selectors, layout definitions, or separate runtime app shell may enter the generated source.
4. Deleting any capability or result binding must result in missing-contract diagnostics, not a fabricated control or observation.
5. Switching modes must not result in a network request, an authority change, or a composition fingerprint change.
6. Reordered observations retain keyed DOM row identity and scroll context. Missing selected records cannot be converted into a different process solely by PID.
7. Failed/invalid responses do not replace previously verified state. Missing CPU/GPU measurements retain explicit availability flags.
8. Production routes and legacy test suites stay unchanged and continue to work; candidate unmount and rollback remain available.

## Install

Apply the cumulative manifest-free ZIP using the existing `python new_patch.py <zip> --dry-run`, inspect `actual.patch` and changed-file hashes, then apply without dry-run. This patch contains P1 plus P2 source, so **do not apply the earlier P1 package after the cumulative patch**. The installer may report `verification: no_reference` because the manifest-free package does not contain a `reference.patch`. Never use fuzz application to mask drift; reconcile local changes first.

After applying, run `python -m tools.mcel_p2_monitoring_frob` and test `/applications/mcel-semantic-task-monitor-generated` on the local Windows-host instance. Promotion remains blocked until all production functionality and hosted acceptance are certified.
