# NanoJev 21-bank learnability monitor v2

## Scope

This is a diagnostic-only update to the isolated 21-bank learnability experiment. It does **not** change the training objective, gradient clipping rule, optimizer, bank weights, prompt generator, dataset splits, checkpoint format, deployment gate, or no-accuracy-abort policy.

Updated files:

- `tools/nanojev_hawk_dove_gaussian_joint_decoder_21_learnability.py` — adds *probe-only* whole-set and per-magnitude signed-asinh MAE of the zero predictor and frozen Stage 2 predictor; no training behavior changes.
- `tools/nanojev_hawk_dove_learnability_monitor.py` — read-only inspection, zero-baseline and frozen-baseline comparisons, true relative nonzero subunit accuracy, gradient clipping, and strictly matched decoder-only versus joint A/B comparison.
- `tests/test_nanojev_hawk_dove_learnability_monitor_v2.py` — regression tests for measured baselines, historical probe compatibility, zero-collapse false positives, identity matching, and high step counts.

## What constitutes evidence

1. **Fitting:** Signed-asinh training error decreases on the controlled 32 examples per tier.
2. **Generalization:** Signed-asinh error decreases on the *distinct* fractional development instances.
3. **Nontrivial numerical learning:** New fractional dev and the nonzero below-1 subset outperform the zero predictor **in signed-asinh error**, with strict 5% relative accuracy increasing. The monitor calls this *evidence*, never proof of the underlying arithmetic rule.
4. **Stability diagnostics:** Reports original-task accuracy drops without stopping, and warns when >=95% of updates in each of the last up to three measured chunks needed clipping. Clip multipliers and magnitude of decoder's signed-asinh correction remain available.
5. **Independent A/B:** Decoder-only and joint must have identical fixed source, teacher, payoff fixture, seeded numerical instances, initialization checkpoint hashes, learning rate, and other listed settings. The monitor then compares their held-out errors only at an identical completed step.

An improvement in signed-asinh MAE by itself may reflect movement toward zero. A project accuracy based on `abs(error) / max(1, abs(X)) <= .05` is **0.05 absolute tolerance for abs(X)<1**, not the strict relative accuracy target. Both remain independently reported.

## Run

In the Windows project root after overlaying `main_computer_test/`:

```powershell
& $py -m tools.nanojev_hawk_dove_learnability_monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_decoder_only"
```

For both arms:

```powershell
& $py -m tools.nanojev_hawk_dove_learnability_monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_decoder_only" `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_learnability_joint"
```

Optional `--history-limit 40` keeps output manageable even with thousands of probes. Optional `--clip-warn-fraction .95` changes the diagnostic warning threshold, not the actual training clip. Complete first/latest probes and the full-run best result remain available regardless of history limit.

## Historical probes

Existing v1 probe files can still be read by this monitor; their missing zero-predictor **signed-asinh** errors are reported as not measured. Existing floor-based zero-predictor accuracy cannot be substituted for these missing transformed-error measurements. Newly generated training probes have the additional fields. The model's running process must load the updated Python module at startup to emit them.

The reader accesses only `probe-step-*.json` and optionally `config.json`. It never opens `.pt` checkpoints, original TEST examples, or model files; it never modifies run directories.

## Interpreting a warning

- `PERSISTENT_GRADIENT_CLIPPING`: optimizer updates were being severely downscaled; not proof of divergence.
- `SUBUNIT_ASINH_NOT_BETTER_THAN_ZERO`: the subunit predictor did not surpass outputting zero under the transformed-error measure.
- `STRICT_SUBUNIT_IMPROVEMENT_NOT_ESTABLISHED`: no endpoint gain on nonzero below-1 strict relative accuracy.
- `ZERO_ASINH_BASELINE_NOT_MEASURED_IN_HISTORICAL_PROBE`: old probes lack the metric; do not infer failure or success.
- `ORIGINAL_TASK_ACCURACY_DROP_OBSERVED_NOT_AN_ABORT`: old-task decline remains visible, but this experiment deliberately does not stop for it.
- `NOT_COMPARABLE`: matched-arm comparison is withheld when config, donor, data identity or initial evaluations differ.

No training exit status or loss value alone proves learnability.
