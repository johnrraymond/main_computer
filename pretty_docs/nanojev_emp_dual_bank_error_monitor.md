# NanoJev four-bank error variance and bias monitor

## Purpose

Add per-bank, per-DEV-view, per-residual-band standard deviations and bias
estimates to the active four-bank outward radius ladder. This is **measurement
only**, not a change to the loss, gates, sampler, or optimizer. The actual
source files SHA-pinned by an existing ladder campaign are unchanged.

The monitor wraps the existing `tools.nanojev_emp_dual_bank_ladder` entry point.
It temporarily augments `summary_for()` with extra descriptive fields, using
the **same decoded predictions already calculated** by the standard DEV probe.
No new model forward passes happen during a training probe, and no TEST cards
are opened during outward acquisition.

## Inspect your current ±1,024 checkpoint immediately (no training)

```powershell
& $py -m tools.nanojev_emp_dual_bank_error_monitor `
    --monitor-inspect-only --monitor-radius 1024 `
    --source-run "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --device cuda --target 4000000 `
    --left-gate 0.80 --right-gate 0.50 --gate-streak 2 `
    --stage-steps 10000 --probe-every 250 --max-stages 16 --resume
```

`--monitor-inspect-only` reads the saved weights and existing frozen DEV decks,
and writes **only** the sidecar statistics. It does not change a training
checkpoint, gate, or training RNG. If the checkpoint has a different radius,
pass that radius or omit `--monitor-radius` to inspect the highest saved stage.

## Continue the ladder with new measurements

Stop the original training process first. Resume using the same campaign
arguments and replace only the module name:

```powershell
& $py -m tools.nanojev_emp_dual_bank_error_monitor `
    --source-run "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --device cuda --target 4000000 `
    --left-gate 0.80 --right-gate 0.50 --gate-streak 2 `
    --stage-steps 10000 --probe-every 250 --max-stages 16 --resume
```

Each subsequent probe prints the original `EMP_LADDER_PROBE` plus a new
`EMP_LADDER_ERROR_STATS` line. The monitor writes:

- `stages/outward_<radius>/error_bias_monitor_v1.json`
- `stages/outward_<radius>/error_bias_monitor_v1.csv`

It also inserts `error_bias_stats` into new `history.json` DEV result rows.
Old probes have no predictions stored, so their variance/bias history cannot
be reconstructed from a final checkpoint. The inspect command captures the
current checkpoint before additional training.

## Metrics and interpretation

For ground-truth signed residual `y` and decoded integer estimate `p`:

| Field | Formula / interpretation |
|---|---|
| Signed numerical error | `e = p - y` (units) |
| `signed_bias` | Mean of `e`: numeric drift above/below the correct signed answer |
| `signed_stddev` | Population standard deviation of `e`, original units |
| Signed relative error | `(p - y) / abs(y)` for `y != 0` |
| `relative_bias`, `relative_stddev` | Mean and population SD of signed relative error; comparable across radii |
| Signed scale error | `p / y - 1` for `y != 0` |
| `scale_bias`, `scale_stddev` | Mean and population SD of signed scale error; negative means estimates tend *toward/past zero* for either sign, positive means they tend *away from zero* |
| Magnitude bias | `abs(p) / abs(y) - 1`, which measures over/underprediction of absolute magnitude even if the sign is wrong |
| `pos_scale_bias`, `neg_scale_bias` | Means calculated separately for positive and negative true residuals |
| `sign_asymmetry` | Positive-target scale bias minus negative-target scale bias |
| Tipping-point violations | `p / y <= 0` for nonzero `y` |
| Zero errors | A separate unnormalized mean/SD over `y == 0`; exact zero never divides by zero |

Every group reports its actual number of cards. The SD uses `ddof=0` because
the fixed DEV deck is treated as the measured population. These statistics are
descriptive; a nonzero measured bias is not automatically statistically
significant, particularly for small DEV groups or correlated samples.

**Why both means?** If positive answers are 20% too large and negative answers
are also 20% too large in absolute magnitude, raw signed errors may cancel.
Scale bias reveals that systematic overestimation. Conversely, a fixed +20
numeric offset creates positive signed-error bias even when magnitude errors
partly cancel across the two signs.

## TEST handling (no leakage)

The ladder is a DEV-only research gate. TEST is not automatically examined
at every training probe. Once a separate sealed audit has produced final
predictions, analyze those saved predictions **offline**:

```json
{"split":"test","rows":[{"bank":"left.left","view":0,"subset":"frontier","target":512,"prediction":500},{"bank":"left.left","view":0,"subset":"frontier","target":-512,"prediction":-490}]}
```

```powershell
& $py -m tools.nanojev_emp_dual_bank_error_monitor `
    --monitor-predictions-json "C:\path\to\opened_test_predictions.json" `
    --monitor-offline-output "C:\path\to\test_error_bias.json"
```

The offline report includes both a pooled summary and `by_bank_view` groups
when records supply `bank`, `view`, and `subset`. The offline function does not generate TEST cards or select checkpoints.
Never feed TEST metrics back into tuning, gate selection, or checkpoint choice.

## Safeguards

- The original ladder, its saved configuration and its existing source checksums are unchanged.
- All four banks use their existing optimizer and training rules; the monitoring code has no gradient.
- Regression tests include an exact model/optimizer/RNG checkpoint comparison with and without monitoring.
- The existing 80% / 50% DEV advancement gate remains identical.
- This is not independent TEST certification and cannot promote a research checkpoint.
