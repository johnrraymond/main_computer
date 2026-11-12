# MCEL Viewport Composition Oracle V2

## Governing invariant

An MCEL application is bounded by its assigned viewport. The application root and browser document **do not scroll**. Scroll ownership belongs to appropriately allocated major navigation and workspace regions, not to every card, form, status panel, or command group. Native widgets such as code editors, logs, and tables may manage internal scrolling only when that need is explicitly declared and supported by matching content semantics.

The prior viewport-scroll frob certified the *ability to scroll inside arbitrary regions*. That was insufficient: a five-column, equal-row layout with individually scrolling 70–270-pixel-high panels passed while presenting an unusable SLH UI. V2 must reject that result even if synthetic overflow tests pass.

## Independent evidence

1. Render the real production MCEL host with normal content, in an isolated browser with all external effects disabled. The normal-content probe runs **before** synthetic overflow is injected. It reads DOM geometry and computed styles but never trusts the compositor's intended plan as proof of quality.
2. Require a legible, dominant primary workspace: at viewports under 680px, it occupies at least 80% of application width and 45% of application height; at wider widths, at least 45% of width and 60% of height. The primary workspace must be within the assigned application viewport.
3. On noncompact layouts with a semantic navigation rail, navigation must occupy at least 65% of application height and remain within its bounds. On compact layouts a deliberately collapsed or disclosed navigation may be shorter; the primary workspace must still be usable.
4. Reject three or more immediate ordinary cards that all occupy the same thin fractional height (under 56% of the application height). This is a direct negative witness for equal-height-card-strip composition.
5. Reject `overflow:auto` or `overflow:scroll` on ordinary control, status, selection, inspection, command, and assistance regions. It is invalid **even when the current card has no excess content**: future content would become trapped in another scrollbar. Only navigation/workspace roles own scrolling by default. A specialized editor/log/table exception requires both an explicit `data-mcel-scroll-owner` value (`editor`, `log`, or `table`) **and** matching content, such as Monaco, a textarea, log `<pre>`, grid, or document. Marking a generic action card as an editor does not exempt it.
6. Reject content hidden behind `overflow:hidden` in a region without an authorized scroll owner.
7. After the normal-content composition assessment, inject synthetic content into the navigation and workspace; verify document, application root, and semantic root stay stationary, the designated regions scroll independently, and neither region becomes inaccessible. Keep horizontal-overflow and independent-position checks.
8. The Applications shell must satisfy the same viewport containment and geometry requirements. Chat Console, which has a different canonical renderer, uses a dedicated browser observation mapping into the same independent Python composition oracle and a dedicated scroll-containment probe.

These thresholds are **acceptance floors**, not an aesthetic score. They may be strengthened if actual workflows expose further problems. A compositor must satisfy these without per-app CSS exceptions or a blanket `overflow:hidden` that clips controls.

## Coverage, safety, and wiring

`python -m tools.mcel_layout_frob --mode viewport-composition` and the legacy alias `--mode viewport-scroll` run the full gate across: SLH local, SLH network, File Manager, Task Manager, Code Editor, Calculator, Generated Monitor, Applications shell, and Chat Console; at 390×650, 1040×768, and 1440×900. The normal smoke mode invokes the same gate before any other layout checks. The paired-application geometry oracle now also invokes the shared composition adjudicator; the SLH unified-administration V4 contract requires identical scroll ownership after local or remote login.

The report `runtime/reports/mcel-layout-frob/viewport-scroll/report.json` distinguishes `containment_passed`, `composition_passed`, each case's `evidence` (synthetic overflow), and `composition` (normal content). Failures save browser screenshots in `failures/`. A failed case is **not** an accepted implementation, even if its containment portion passes.

No SLH, file, process, or network operation is executed. The frob is isolated browser/markup evidence, **not** live operations certification. Other legacy views not rendered by these hosts remain unverified.

## Expected first run against the November 10, 13:51:53 snapshot with V2 scroll patch

**RED**: the current shared stylesheet still assigns `grid-auto-rows:minmax(0,1fr)` and `overflow:auto` to generic child regions. The Applications shell should pass; the other semantic application compositions will not. Chat Console additionally fails document-level containment. This is the desired initial signal. Do not change thresholds merely to restore a green report. Correct shared MCEL composition, then rerun the gate and only promote the result when all cases pass.
