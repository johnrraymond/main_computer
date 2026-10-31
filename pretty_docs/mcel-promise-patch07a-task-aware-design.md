# MCEL Patch 07A — Task-Aware Design Planner

Date: 2026-10-30. Scope: source-driven application shell only.

## Governing objective

Make MCEL a stable and useful application interface. Human-hour productivity benchmarks, comparative authoring economy and invented aesthetic numbers are **not** acceptance requirements. The candidate's utility is demonstrated with actual rendered behavior and human review. No third application, alternative DSL or domain-specific CSS was introduced.

## Installation and selection

The production path remains **legacy by default**. Task Manager and File Explorer both accept an additional query parameter `mcel_planner=task-aware`, only when their existing generated-application modes are already enabled. Remove that parameter to restore the existing composition. The legacy original applications are still available via their established rollback URLs.

- Task Manager: `/applications/task-manager/server-processes?mcel_task_notebook=application&mcel_design=operational&mcel_planner=task-aware`
- File Explorer: `/applications/file-explorer?mcel_file_explorer=candidate&mcel_design=operational&mcel_planner=task-aware`

The shared mount API uses `options.planner`, accepting only `legacy` and `task-aware`; omitted means `legacy`. Both presentation modes remain separately selectable without remount. Source compilation, canonical IR, authorization and effect/receipt infrastructure are unchanged.

## Semantic decisions and evidence

`profileContent()` samples at most 32 records per populated collection, reading only author-declared columns. It preserves every delivered record: sampling is **only for a bounded design profile**, not rendering or behavior. Profiles bucket collection sizes (`small` up to 8, `medium` up to 40, `large` above 40) and representative display-text lengths (`short` below 30, `medium` below 72, `long` 72 or more characters). Exact counts and text are not stored in the design record.

`taskAwarePlan()` builds on the existing Wave 4 `designPlan()` and adds separate `planRole`, `priority`, `density`, `extent` and `reasons`. Each reason points to a semantic declaration or observed content bucket. This is runtime output, not author-facing DSL. The Wave 4 `role` and `prominence` contracts remain unchanged; only one new `priority:primary` is assigned. Content cannot grant domain authority or authorize an action.

The first nonempty data for a collection determines a **per-collection latch for the lifetime of the mount**. Later updates never cause plan oscillation from counts or text changes, even crossing thresholds. Switching profiles regenerates the presentational decision from the same latched profile, and restamps existing DOM attributes rather than replacing any region or collection row. `getState().design` returns the exact installed plan, not a recomputed hypothetical plan.

## Geometry and scroll ownership

CSS is gated on `[data-mcel-planner="task-aware"]`, and uses additive `data-mcel-plan-*` attributes. The task-aware primary collection has a taller table viewport; large sidebar collections receive a limited navigation frame; wide tables preserve their own horizontal and vertical scroll. The **table frame remains the scroll owner**, and the shell retains its existing scroll. The candidate determines size from the **actual embedded shell height**, never `vh` or global window height; a resize listener/observer updates only CSS variables and is removed at teardown. The original 225px CSS cap remains unchanged for legacy. No DOM structure or table record truncation was introduced.

## Known limits

Task Manager's principal workspace is an embedded notebook slot. This patch deliberately does **not** infer the notebook's internal semantics or manipulate its DOM. Thus the principal measurable geometry improvement is File Explorer's primary directory collection; Task Manager primarily demonstrates stable slot, unchanged operations and compatibility. Task-aware plan quality is still subject to a human visual/usefulness review. This is initial-planner work, **not** Patch 07B automatic visual critique or remediation.

## Verification and operator commands

Run the new Frob (offline browser evidence even without localhost) and existing Wave 4, Wave 5, closure checks:

```powershell
python -m tools.mcel_promise_wave7a_frob `
  --task-manager-url "http://127.0.0.1:8765/applications/task-manager/server-processes" `
  --file-explorer-url "http://127.0.0.1:8765/applications/file-explorer"
python -m tools.mcel_promise_wave4_frob --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes"
python -m tools.mcel_promise_wave5_frob --host-url "http://127.0.0.1:8765/applications/file-explorer"
python -m tools.mcel_promise_closure_frob
```

The Wave 7A Frob compares **both applications, both presentation profiles, five widths, and legacy vs candidate**. It records browser errors, frame height, row height, visible record estimates, full source parity and stable identity, profile switching without remount, scroll/overflow, receipt and original UI rollback. It writes screenshots and `runtime/reports/mcel-promise-wave7a/report.json`. Optional localhost checks are reported separately; offline PASS must not be mistaken for hosted PASS. Existing old Waves 4 and 5 must stay green. On the 181-record offline fixture, the primary collection should show more useful rows while maintaining **181/181** rendered records and unchanged operation behavior.

## Rollback

Remove `mcel_planner=task-aware` from URLs (no data migration). To remove the patch entirely, restore the pre-07A shared shell JS/CSS and bridge integration files from the patch system backup. No semantic file, capability, data source or persistent schema is modified.
