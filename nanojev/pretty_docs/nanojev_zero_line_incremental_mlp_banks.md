# NanoJev — Independent incremental MLP banks versus existing Gaussian banks

## Purpose

This is a **new, separate experimental runner**. It does not overwrite, patch,
run, or depend on the saved outputs of `nanojev_zero_line_incremental_banks`.
The original Gaussian runner and all of its checkpoints remain untouched.

The exact research question is whether a conventional MLP specialist can learn
signed residuals 16..32 (and optional larger intervals) better than the
existing *independently trained Gaussian specialist* under the same data,
optimization budget, and routing/retention gates.

### Controlled architectural comparison

Both isolated specialists use the certified radius-127 backbone's frozen
activations. Both learn a signed scalar coordinate `z` from those activations
and receive the same auxiliary signed-residual coordinate loss. Their
last numerical classifier differs:

- **Gaussian incremental specialist:** combines the frozen features, `z`, and
  Gaussian radial-basis activations in the signed interval.
- **MLP incremental specialist (this runner):** combines the same frozen
  features and `z` directly into a parameter-matched nonlinear MLP. It has
  **no Gaussian kernels, centers, sigmas, or radial-basis activations**.

The MLP width is automatically chosen to match the Gaussian specialist's
trainable parameter count (approximately equal, within 1% for normal backbone
widths). Both retain the same seven-place digit and sign objective, and the
same 0.5-weighted coordinate loss. Learned gates receive frozen backbone
features, never oracle residuals or known bank IDs. There are no external
arithmetic executors.

The two experiments have separate outputs, checkpoint schemas, and command
names. The MLP runner incorporates a *matching copy* of the Gaussian runner's
sampler, evaluator, gating and acceptance code, so the original Gaussian module
is not changed. It uses upstream zero-line feature caching and sampling files
already installed from the earlier patches.

## Running one interval (recommended initial comparison)

### A. Gaussian reference — original unchanged harness

If you already have an incremental Gaussian run using these exact flags, reuse
its existing output folder. Otherwise run:

```powershell
& $py -m tools.nanojev_zero_line_incremental_banks `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_gaussian_16_32_v1" `
    --device cuda `
    --intervals "16:32" `
    --steps-per-bank 750 `
    --router-steps 400 `
    --probe-every 150 `
    --batch-size 128
```

### B. MLP — entirely separate new runner

```powershell
& $py -m tools.nanojev_zero_line_incremental_mlp_banks `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_mlp_16_32_v1" `
    --device cuda `
    --intervals "16:32" `
    --steps-per-bank 750 `
    --router-steps 400 `
    --probe-every 150 `
    --batch-size 128
```

**Train each arm separately** with the same `--seed` (default 20261025),
`--intervals`, data sample counts, training steps, batch sizes, learning rates,
and acceptance thresholds. Do not reuse an output folder. Do not compare this
MLP experiment to the *older joint Gaussian-radius* harness: its protocol is
not the independent-interval one.

### C. Verified comparison — no retraining

```powershell
& $py -m tools.nanojev_zero_line_incremental_mlp_banks `
    --phase compare `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_mlp_16_32_v1" `
    --gaussian-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_gaussian_16_32_v1"
```

This writes `comparison.json` **inside the MLP output folder only**. It
verifies source SHA256, signed intervals, train/OOD bands, sample/optimizer
configurations, and population counts. It fails closed on mismatches instead
of silently comparing incompatible experiments.

## Extend to the full set of banks

Use the identical flags in both experiments:

```powershell
--intervals "3:15,16:32,33:64,65:128"
```

`3:15` is a bridge: **a reliable ±15 residual base has not been
established**. A standalone `16:32` experiment tests an isolated outer bank;
it cannot establish full-range zero-line competence by itself.

## What is scored

- Isolated TRAIN/DEV exact sign and seven-digit accuracy for each bank.
- Isolated DEV/OOD **per signed residual × magnitude band × orientation**;
  no partial interval can pass a whole-interval certificate.
- Learned-router composite performance separately from oracle-isolated
  specialist performance; no oracle router is used at inference.
- All 65,025 original radius-127 cards for **unguarded** historical
  retention, plus separately labeled guarded retention.
- Whether the new bank damages previously correct DEV/OOD predictions in
  the near-zero (-2..+2) region or previously promoted bank intervals.
- An interval is promoted only if DEV and OOD strict certification pass,
  historical retention is 100% *without* the optional operand guard, DEV
  accuracy improves versus incumbent, OOD doesn't regress, and no protected
  prediction is newly incorrect.

The comparison JSON contains matched per-interval `mlp` and `gaussian`
results, their signed differences, and the ratio of trainable specialist
parameters. The comparator does **not** claim independent statistical
replication; train initialization differs by architecture even under a shared
seed, and stochastic/GPU differences may require repeated seeds.

## Files

- `tools/nanojev_zero_line_incremental_mlp_banks.py`: new standalone MLP
  bank trainer + JSON comparator.
- `tests/test_nanojev_zero_line_incremental_mlp_banks.py`: CPU and comparison
  tests, including checkpoint immutability, no-oracle inference, unchanged
  historical correctness checks, and mismatch refusal.
- `pretty_docs/nanojev_zero_line_incremental_mlp_banks.md`: these instructions.

Outputs are separate: `result.json`, `config.json`, per-bank
`specialist.pt`, `candidate.pt`, and only when eligible `promoted.pt`.
`comparison.json` is written only by the explicit comparison phase or when
`--gaussian-output` is supplied to the MLP training command.

The certified source weights, Gaussian files/results, test set, and HybridLink
remain unchanged. CUDA research results must be measured on the workstation;
CPU smoke tests are not evidence of model generalization.
