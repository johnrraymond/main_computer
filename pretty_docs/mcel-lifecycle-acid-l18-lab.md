# MCEL Lifecycle Acid Lab — L18 screening and drill-in

This experimental workbench is visible directly in `/applications/mcel-lab`, beside the existing MCEL element acid test. It uses the **real universal semantic collection, layout, negotiation, source-observation, selection-continuity, and retirement code** mounted on offline read-only Task Manager semantic fixtures. It is not a replacement application and cannot send process-control operations.

## Top-level Taguchi screen

The matrix is an L18 mixed-level orthogonal array: 18 configurations with one two-level factor and seven three-level factors. Factors: data density (normal/stressed), reveal trigger (select/hover/edge), source observation (unchanged/partial/authoritative removal), interaction (idle/native selection/floating inspection), host capability (all/no scroll/no overlay), retirement timer (resume/reset/continue), container width (360/620/900), and identity evidence (verified/unverified/replaced generation). The final L18 column is derived using a balanced constrained assignment; the browser independently validates every pair of factor columns for exact orthogonality before exposing the experiment. References: Minitab Taguchi array catalogue, especially L18 (2¹ 3⁷).

Run `Run L18 screening` with an explicit seed and 1–5 independent trials per configuration (default 2). The seed **randomizes execution order**, not the array's mathematical assignments. Each trial mounts a fresh MCEL presentation, requests expansion/hover, injects the specified source and interaction events, checks the actual fallback choice and retirement state, rejects stale observation sequences, injects a transaction failure, and then checks source identity, layout, and record membership. Execution timings are browser cost proxies, **not** human time-to-answer. Failed invariants fail the trial; an expected capability refusal does not.

The table reports the full factor assignments, status and timing. The aggregate displays count of failed configurations and average measured time for each factor level. With only 18 configurations, main effects are preliminary and interactions remain confounded. Green means **the listed probes passed**, not that this factor setting has been comprehensively certified.

## Drill-in

Click `Drill in` on any configuration to prefill its eight factors and show the run's trace. `Mount focused live specimen` renders a persistent MCEL collection with the configured width, source identity and host policy. Use the real controls to inspect selection, hover, source updates, removal, scrolling, stale observations, and intentionally failed transactions. Native text selection operates in the actual browser DOM. `Wait 2.2s for retirement` lets timers expire under the current protection state. The specimen can be released without changing Task Manager.

`Run controlled pairwise drill` executes a local 2×3 or 3×3 comparison using two selected factors while holding other factor levels fixed. This is a **conditional drill-in**, not another orthogonal array or an independent interaction-effects estimate. Results and exact traces are exported as JSON; no report is automatically uploaded or promoted.

## Reproducibility

`python -m tools.mcel_lifecycle_taguchi_acid_frob` runs the equivalent genuine MCEL browser test in offline Chromium at 390px and 1440px, writes `runtime/reports/mcel-lifecycle-taguchi-acid/report.json` and creates independent screenshots. `pytest tests/test_mcel_lifecycle_acid_lab.py -q` includes source-wiring, orthogonality, and live-browser controls.

## Deliberate limitations / follow-ups

This first increment **does not implement a virtualized list host**, a general atomic coordinator for all legacy paths, true worker concurrency, cross-browser accessibility certification, or real human interaction studies. It makes these gaps explicit rather than allowing the L18 matrix to certify them. The next drill-in families should add virtualizer lifecycle, delayed promises/worker messages, rollback failures, nested scrolling, overlay collision, and security invalidation. Every new family should add a negative control that proves its oracle detects a deliberately introduced defect.
