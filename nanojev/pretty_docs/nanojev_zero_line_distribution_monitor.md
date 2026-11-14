# NanoJev: fixed-card residual-distribution convergence monitor

## Purpose

Measures **how each new residual regime changes during learning**, not merely
whether the model meets a fixed 5% threshold. At the start of each stage, and
after each ordinary trainer probe, it records the normalized prediction ratio:

`q = predicted_signed_integer / true_signed_residual`, for nonzero residuals.

- `q = 1`: exactly correct.
- `0 < q < 1`: right side of zero but underestimating magnitude.
- `q > 1`: right side but overestimating.
- `q <= 0`: collapsed onto or crossed the zero tipping point.
- For true residual zero, report *decoded-zero errors* separately (no division).

The distributions are across **fixed held-out DEV cards**, not the model's own
per-card uncertainty distribution. No targets, oracle outputs, or residual labels
enter the neural network; they are evaluation data only.

## Design and compatibility

The patch introduces a **sidecar command** instead of editing
`tools/nanojev_zero_line_outward_return.py`. The original training program embeds
its own source SHA-256 in `config.json`, so editing it would invalidate the
already-running campaign. The sidecar instruments its in-process checkpoint
snapshot call, with a `finally` block that restores the unpatched callable. It
does not alter the existing learning rate, optimizer, RNG, training cards,
retention gates, or demonstrated 80% advancement policy. It reads **DEV folds
only**, never the sealed test. It uses local deterministic RNGs and fixed cards.

At a stage's first instrumented probe, it evaluates the **parent weights** as a
step-zero baseline, even when monitoring starts after an earlier probe in an
already-running stage. Previously completed probes cannot be reconstructed from
only the latest checkpoint and are not invented.

On resume, when monitor history contains a probe beyond the trainer's latest
checkpoint (possible if a process was interrupted between two atomic writes),
the monitor drops uncommitted entries and recomputes them from resumed training.
Its own config and card fingerprints are verified before appending.

## Run on an existing campaign

Stop the old training process cleanly. Apply the ZIP rooted under
`main_computer_test/` in the repository. Then resume **the same campaign** by
replacing only the Python module with the sidecar entrypoint, retaining every
original argument exactly, and appending `--resume`:

```powershell
& $py -m tools.nanojev_zero_line_distribution_monitor `
    --phase outward `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --zero-only-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_zero_only_v1" `
    --r16-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius16_v1" `
    --r32-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius32_v1" `
    --warmstart-stage "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_sampled_radius_ladder_v1\stages\radius_64" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_outward_return_80pct_v1" `
    --device cuda `
    --target 4000000 `
    --outward-policy demonstrated `
    --outward-min-outer 0.80 `
    --outward-min-frontier 0.80 `
    --stage-steps 10000 `
    --probe-every 250 `
    --max-stages 17 `
    --resume
```

Use exactly the previous campaign settings; omit or retain other args only if
their resulting parsed values are unchanged. A new campaign omits `--resume`
and uses an empty output directory, as the original trainer requires.

Optional independent monitor settings (default values in parentheses):
`--monitor-frontier-count 96`, `--monitor-breadth-outer 12`,
`--monitor-eval-batch 256`. These do **not** affect training. Use the same
monitor options on every run against the same folder.

The same entrypoint also supports `--phase return`. Final historical audits,
test sealing, and advancement remain wholly owned by the original trainer.

## Outputs

Per stage, for example `.../stages/outward_512/`:

- `distribution_monitor_v1.json`: immutable stage-parent baseline plus all
  collected snapshots, two independent DEV views, fixed card fingerprints,
  grouped signed residual and operand magnitude statistics.
- `distribution_frontier_v1.csv`: one row per probe × DEV view, also step zero;
  suitable for spreadsheets or charts.
- `distribution_frontier_v1.svg`: viewable plot of the 5th–95th percentile band
  and median prediction ratio, with horizontal target `q=1` and tipping line
  `q=0`. The display clips extreme values to retain readability; raw values are
  unchanged in JSON and CSV.
- `ZERO_OR_DISTRIBUTION=` console line: compact overview (median, q05, q95,
  p90 absolute relative error, tipping-crossing fraction, within-5% rate,
  descriptive trend) alongside the ordinary `ZERO_OR_PROBE=` log.

The raw JSON also includes 1%, 10%, and 20% accuracy thresholds, error
quantiles, and subdivisions by positive/negative sign, operand-magnitude band,
residual shell, and cancellation depth.

Trend labels are **descriptive only**:
`insufficient_probes`, `contracting`, `mixed_or_expanding`,
`plateau_or_biased`, and `stabilizing_near_target`.
Narrowing around the wrong answer is *not* success. The monitor does not change
training decisions or claim a mathematical proof. It does not convert the
previously studied DEV folds into untouched certification evidence.

## Validation

Run:

```text
python -m pytest -q tests/test_nanojev_zero_line_distribution_monitor.py
```

No new third-party Python dependency is required beyond those already needed
for the original trainer. Actual saved CUDA checkpoints should be tested by
resuming the user's current campaign; unit tests use CPU fixtures.
