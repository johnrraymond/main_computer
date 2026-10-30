# MCEL Patch 14.7.7e — Hover Motion / Scroll Arbitration Diagnostics

## Purpose

**Instrumentation only.** This patch does **not** claim to repair the remaining real-browser hover jumps, position drift, or imperfect scroll handoff. It records the facts necessary to find those defects without rebuilding the hover implementation.

Built against `main_computer_test-20261030-000913.zip` after patch 14.7.7d.

## Usage

1. Open MCEL Lab → Lifecycle Acid Test → L18-01.
2. Click **Record cursor + hover**.
3. Reproduce a jump, spontaneous move, shrink, or incorrect scroll handoff. Try wheel scrolling while the pointer remains over the expanded item, then move into its external scrollbar.
4. Click **Stop cursor + hover**, followed by **Export cursor trace**.
5. Attach `MCEL-L18-01-cursor-hover-trace.json` for a timeline-based diagnosis.

The downloadable JSON uses `mcel.hover-pointer-trace.v2` and contains `events`, `summary`, and contemporaneous Chrome `performanceFindings`. Capturing is local-only and opt-in. No rrweb or remote service is required. Stop retains the events for export.

## New evidence

- `pointer-hit-region-change`: cursor crosses the original row, external rail, or outside, including actual hit-testing coordinates.
- `wheel-before`, `wheel-after`: scroll positions on the original row, parent collection, and document, joined by `seq`.
- `host-wheel-arbitration`: reason (`row-scroll-consumed`, `boundary-arbitration`, or `parent-owned-forward`), delta, momentum guard, overscroll accumulation, 15% threshold, and parent-consumed delta.
- `geometry-baseline`, `geometry-delta`: row, invisible anchor, scrollbar, parent, and page geometry; separate moved-with-anchor versus unexplained row movement.
- `original-fields-shifted` and `original-fields-restored`: visual field geometry changes relative to the row. These events do not include field text.
- `geometry-invariant-warning`: actual rendered scrollbar edge/top/height is disconnected from the expanded row.
- `host-geometry-apply`, `host-geometry-replan`, `host-follow-repair`: the host's requested styling versus observed/browser positions and *why* it recalculated or repaired them.
- `frame-gap`: a delayed frame, which can correlate with `chrome-performance` findings.
- `paint-frame`: reduced-frequency heartbeat, with high-frequency **change** events instead of excessive identical samples.

The export also counts event kinds, reports dropped old events if the bounded ring fills, reports the maximum observed rail gap and frame gap, and includes the last 100 rail alignment alerts. The buffer defaults to 10,000 events and is configurable through `MCELDiagnosticPlugins.configure` while recording is disabled.

## Gates

```powershell
python -m tools.mcel_hover_motion_diagnostics_frob
python -m tools.mcel_hover_pointer_plugin_frob
python -m tools.mcel_hover_live_geometry_frob
```

The first test **intentionally introduces a 22px scrollbar defect** in an isolated browser fixture and requires the plugin to detect it; the fixture is discarded after verification. The other tests confirm the existing hover and plugin gates remain intact. This patch does not claim the broader lifecycle suite is cleared.

**Important:** The recorder itself reads layout geometry while enabled and can add overhead. It is disabled by default. For performance comparisons, use a non-recording baseline and a recording run.
