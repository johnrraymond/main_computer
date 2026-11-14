# NanoJev zero-line Gaussian radius expansion

## Purpose

Test whether the *already-certified* radius-127 digit-token recurrent backbone contains information sufficient to support a wider signed-residual neighborhood using a trained readout. Preserve the original backbone, the original output heads, and all 65,025 original flash cards. Do **not** train Z, build a task router, or delegate addition to an external arithmetic executor.

This is an experiment, not a claim that Gaussian banks work. It branches from `rungs/radius_127_passed.pt` and leaves that file unchanged. It requires the existing `nanojev_zero_line_ladder.py`, `nanojev_zero_line_radius255_probe.py`, and `nanojev_zero_line_head_radius_growth.py` modules from previous patches.

## Compared neural readouts

All three trainable adapters receive the same frozen recurrent query vector and the original frozen sign/digit logits. They learn corrections to those logits, initialized **exactly zero** so all arms initially reproduce the certified source:

- `mlp`: fixed-capacity MLP with approximately 61,897 parameters for a width-64 source.
- `gaussian`: four nested Gaussian-resolution populations, with a shared **learned** one-dimensional residual coordinate; approximate parameter count 58,597.
- `routed`: the same multiscale populations plus a trainable softmax mixing mechanism driven by the frozen state, approximately 58,857 parameters.

Gaussian centers span [-16,+16], [-32,+32], [-64,+64], and [-128,+128], with Gaussian widths scaling with each range. Later stages activate additional banks. A bank's input coordinate is **not** the true answer: it is predicted by a linear map of the frozen neural representation. The routed arm's weights also depend only on the same learned neural representation. Output computation never adds or subtracts the input operands.

## Data isolation

Supervision for TRAIN and scoring for DEV use exact Python integer addition. This oracle never enters model inference. Data are stratified by signed residual, operand-magnitude band, and the sign of the first operand. TRAIN and DEV use disjoint unordered absolute-magnitude operand families, including mirrored signs and swapped inputs. A separate OOD set uses previously unseen magnitudes 256–511. The original, mastered radius-127 deck is fully evaluated for retention at every probe and also sampled directly during training.

The stage sequence expands **signed residual radius**: ±16, ±32, ±64, ±128. It does not mean the operand radius is being expanded in training. These are separate axes. Every stage mixes frontier examples, center examples and other covered residuals; half the batch by default comes from the original deck. A KL penalty discourages drifting away from the frozen source outputs on those historical examples.

## Run

Apply the ZIP at the Main Computer repository root, retaining the `main_computer_test/` paths.

```powershell
& $py -m tools.nanojev_zero_line_gaussian_radius `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_gaussian_radius_v1" `
    --device cuda `
    --arms mlp,gaussian,routed `
    --max-residual 128 `
    --steps-per-stage 750 `
    --probe-every 150 `
    --batch-size 128
```

To continue **an interrupted run with unchanged settings**, repeat the command with `--resume`. Do not change the step budget or sample settings on resume; config drift is refused. For a quicker first diagnostic, use a fresh output directory, `--max-residual 32 --steps-per-stage 300 --probe-every 100`; that shorter run is a separate experiment, not a resumable prefix of the 128 run.

## Logs and saved results

Each probe reports:

```
ZERO_BANK arm=... stage=±... step=... loss=... DEV_r=... OOD_r=... DEV=... OOD=... retain=... eligible=...
```

Per-arm files include `baseline.json`, `result.json`, `latest.pt`, `summary.json`, and `stage_<R>_dev_cells.csv` / `stage_<R>_ood_cells.csv`. The CSVs report individual signed residual × magnitude band × orientation cells. Gaussian arms also save `stage_<R>_routing.json` with mean mixture weights by residual at the end of each stage. `best.pt` and `best.json` are written **only** when every promotion condition is true.

The gate for DEV radius `r` requires every integer residual from -r through +r to meet an 80% exact sign-and-digit accuracy threshold independently in every operand band and orientation (at least 6 samples per cell by default). Missing or undersampled cells fail closed. The contiguous interval starts at zero and cannot jump over a failed cell.

**A checkpoint is eligible for promotion only when:**

1. Its DEV certified radius exceeds the original frozen-readout baseline.
2. All **65,025** previously mastered ordered cards remain *exactly* correct; no 99.9% concession is permitted.
3. OOD certified radius and aggregate OOD accuracy do not regress relative to the baseline.

The original checkpoint itself is never overwritten. `best.pt` may legitimately be absent: that is a failed experiment, not a crash. `latest.pt` is only a resumable experimental state and must not be treated as certified.

## Limits on interpretation

Head-only improvements demonstrate that the frozen representation contains information extractable by a new readout. They do **not** prove a general arithmetic invariant, and a zero-line success is not automatic Hawk–Dove X/Y or HybridLink certification. A head that only memorizes TRAIN, or achieves higher aggregate DEV accuracy without increasing its contiguous signed-radius gate, has **not** met the research objective. TEST and Z remain untouched.

## Tests

```powershell
& $py -m pytest -q tests/test_nanojev_zero_line_gaussian_radius.py
```

The tests use a synthetic shape-valid certificate and intentionally shortened deck **only within test mocks** to verify mechanics; this does not certify arithmetic competence. Production always uses the full radius-127 deck. The test suite covers separate numerical scales, learnable routing, frozen-activation caching, data isolation, zero-initialized adapters, strict gates, deterministic resume, failure to overwrite the source, and CPU end-to-end execution.
