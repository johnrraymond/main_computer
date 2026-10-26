# MCEL Design Quality Measurement Validity Frob v3

Research-only, with no AI critic in the runtime path and **no production MCEL layout, application-package, or scoring-weight changes**. This extends the existing `design-quality-stress` experiment with validity witnesses, not new preference weights.

## Run

```text
python tools/mcel_layout_frob.py --mode design-quality-stress
python -m unittest tests.test_mcel_design_quality_validity tests.test_mcel_design_quality_stress tests.test_mcel_design_quality_frob -q
```

The runner requires locally available Playwright and Chromium/Edge/Chrome. The source Task Manager experimental design remains under `mcel_designs/task-manager/`; it must not be moved into `mcel_apps/`. There is no need to start the Main Computer viewport server to run the offline experiment.

Results under `runtime/reports/mcel-layout-frob/design-quality-stress/`:

- `validity-report.json` — checks whether each declared perturbation visibly happened; groups candidates with equivalent browser geometry, identifies unsupported task objectives and confounded score changes.
- `stress-report.json` — full original hard-gate measurements **plus** rendered witnesses, v3 validity findings, original unvalidated decisions, and guarded decisions.
- `stress-gallery.html` — all 54 captures together with reasoned findings, including cases where v3 abstained.
- `stress-review.zip` — portable evidence, including `validity-report.json`.

### What v3 measures

1. **Fixture effectiveness:** rendered record strings and counts, visible extra tools, computed visible font sizes, selected inspector visibility and field count, verified-failure and unknown-state truth. A dataset flag alone is insufficient. Long-text fixtures must actually change *visible record text*, rather than a hidden node.
2. **Spatial equivalence:** source-independent fingerprints of actual DOM region and record geometry. When top candidates are geometrically identical, there is no meaningful layout competition (`EQUIVALENT_LAYOUT`). Pixel-equivalent images are reported separately.
3. **Task evidence:** first record access for browsing, failure feedback access for recovery, selected inspector visibility, field count and scroll depth for inspection. The old quality function has no inspection objective; inspection and unknown-outcome comparisons abstain (`UNSUPPORTED_TASK`) rather than borrowing browsing scores.
4. **Score causality:** detect when adding records increases utility solely by enlarging the page's denominator in the *tools-area fraction*, without changing tools' size/content or primary-workspace accessibility (`PAGE_HEIGHT_SCORE_LEAK`). The original score remains visible for diagnosis but its winner is withheld (`SCORE_CAUSALITY_FINDING`).

The stress experiment still applies hard constraints before any ranking. An ineffective or unverifiable fixture is reported as `INVALID_FIXTURE` and cannot establish a winner. `NEAR_TIE` and `EQUIVALENT_LAYOUT` never claim visual superiority. `STRESS_COMPLETED_WITH_DEFECTS` indicates the research **ran and identified issues**; it is not certification of the generator or attractiveness.

### Scope and limitations

These are synthetic Task Manager A/B/C variants and can be used to falsify weak evaluation claims, not to establish broadly correct design preferences. Font enlargement is not a complete accessibility audit. The v3 guard does not repair the known hidden long-text fixture or rewrite the v1 page-height-normalized tools-area score. Those need separate deliberate patches. The existing application server and valid calculator/code-editor package catalog are not modified.
