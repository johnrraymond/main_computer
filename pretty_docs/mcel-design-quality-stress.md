# MCEL Design Quality Stress Frob (experimental)

**Goal:** Try to falsify the existing deterministic A/B/C quality policy before considering its promotion. The existing policy, weights, component registry, renderer, Task Manager, and Website Builder are unchanged. ChatGPT and Gemma reviews are not runtime inputs.

## Run

This compatibility release preserves the task-manager design isolation: experimental YAML resides in `mcel_designs/task-manager/`, not `mcel_apps/task-manager/`. The CLI mode is merged with the isolation repair instead of overwriting it.


From `main_computer` with Python, PyYAML, Playwright and locally usable Chromium/Edge/Chrome:

```text
python tools/mcel_layout_frob.py --mode design-quality-stress
python tools/mcel_design_quality_stress.py --mode run
python tools/mcel_design_quality_stress.py --mode evaluate --review-root runtime/reports/mcel-layout-frob/design-review
python -m unittest tests.test_mcel_design_quality_stress -q
```

The default `run` rebuilds frozen A/B/C candidate HTML through the existing semantic compiler and design critic, proves browser behavior, and opens each unchanged generated candidate in a fresh independent Chromium page. The `evaluate` mode uses an existing design-review directory with actual `generated-A/B/C/preview.html` and `packet.json`; a standalone portable screenshot ZIP cannot substitute for the executable DOM.

## Preregistered conditions

The 18 condition fixtures cover: 320/390/768/1440 CSS-pixel viewports, ordinary and crowded collections, long text, synthetic heavy tools content, 150% font size, selected-item inspector, a compound mobile case, verified operation failure, and unknown outcome. Each condition renders all three candidates. Experimental stress modifications (font size and dense tools content) occur only in isolated browser pages. They never alter production semantic state or authorize actions.

A successful run produces `runtime/reports/mcel-layout-frob/design-quality-stress/` with:

- `stress-report.json`: **all** 54 browser measurements, hard failures, score margins, near-ties, unknown-context abstentions, comparisons and source hashes.
- `stress-gallery.html`: full-page captures with candidate evidence and decisions.
- `screenshots/*.png`: screenshot for every measured candidate/condition.
- `stress-review.zip`: portable report, gallery and screenshots.
- `design-review/`: unchanged MCEL semantic generator HTML and design-critic packet for exact replay.

## Acceptance and non-acceptance

**Hard correctness gates** reuse the previous independent measurement code: canonical runtime ready, registered element count unchanged, search co-located with the collection, no horizontal overflow, visible record width above the experimental 240px floor, long-token checks, and verified-failure truth. Additional gates require selected-item disclosure and unknown-outcome honesty. A renderer failure or missing browser evidence refuses; it never becomes `PASS`.

The old versioned task scores remain **exactly as they are**. No candidate ID or AI review enters the score. A preregistered absolute margin below `0.02` is reported as `NEAR_TIE`, **without a declared winner**. A sole feasible design is `SINGLE_FEASIBLE`, not a quality victory. An unknown operation outcome is `UNSUPPORTED_TASK` because the v1 policy only defines browse/recover objectives. All-infeasible groups refuse. Selection reversals relative to the same-width browsing baselines are recorded rather than silently penalized.

`STRESS_COMPLETED_WITH_DEFECTS` is a valid **completed research run**: it is not production certification. Human aesthetic approval stays `PENDING` and production policy remains unchanged. In particular, this Frob does not prove the chosen layout looks attractive, that live authorization works, or that the observed rankings generalize to unseen applications. Task Manager A/B/C share one candidate family; the next certification gate needs genuinely independent held-out designs and human preferences.

## How to interpret the findings

A near tie indicates that the score is too close to distinguish designs under its *own experimental scale*. It is not a statistical significance test. Font scaling is an adversarial CSS text-size perturbation, not a full system accessibility zoom certification. Dense rail content is synthetic; it tests whether space penalties respond to actual content growth, not whether more content is semantically worthwhile. Equal scores at tablet width reveal limited candidate diversity. Review these counterexamples before changing weights.
