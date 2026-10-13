# NanoJev: two-number full-token activation recovery

## Objective

Reconstruct two signed numbers from **one frozen Qwen3-0.6B prompt** with a literal newline separating the numbers. The ground-truth pair `(x1, x2)` is decoded as `(x1_hat, x2_hat)` from the prompt's full token-level hidden states.

Every **nonzero** reconstructed value must satisfy `abs(x_hat - x) / abs(x) <= 0.05`. True zero must reconstruct as zero. A pair passes **only if both** positions pass. Accuracy is measured *in sample* on the captured ordered pairs, as requested for the current discovery experiment. Training labels are never inserted into decoder inputs.

Three fully independent input formats:

```text
Decimal:     2000000
             -3

Log-Bar:     ||||||||||||||.|||||
             -|.||||

Combined:    2000000, ||||||||||||||.|||||
             -3, -|.||||
```

These are schematic log examples; the script deterministically generates exactly the same codes as `nanojev_multiscale_activation_decoder.encode()`. There is one prompt and one model forward pass per distinct two-line string. Nothing fitted in one representation is reused in another representation.

## Experiment design

- Default 2,000 ordered pairs sampled reproducibly from the existing 507-value grid spanning ±2,000,000. The grid remains dense near zero and progressively sparser at large magnitudes.
- Exactly half are reversed-order counterparts of other pairs. Every original grid value appears in *both* positions at the default 2,000-pair size. The pair bank includes zeros, matching numbers, opposite signs, similar magnitudes, and extreme magnitude ratios.
- Qwen is frozen. All 29 hidden-state positions are captured and saved for **every input token** (`[layer,token,neuron]`). No token mean, PCA, or other dimensionality reduction is performed before regression.
- At decoding time, selected layers (default `16,28`) are right-aligned with masked/padded leading token slots and flattened into a fixed-width matrix. Padding is zeros; actual token states remain intact.
- For each representation, fit separate sparse hierarchical decoders for x1 and x2, each using the *same full prompt activation matrix*. Both heads learn their own sign, zero, near/far routing, and magnitude regression from scratch.
- Start with two magnitude regimes and, if the **joint pair** target is unmet, try up to six regimes from the established hierarchy. This runs independently per penalty strength and representation.
- LASSO uses true L1 regularization, with `alpha = alpha_ratio * head_specific_alpha_max`. The number of surviving coefficients is **measured** and reported, never targeted at 117. Default alphas `0.03,0.015,0.005,0.002`; customize with `--alpha-ratios`.
- Local scikit-learn convergence warnings are consolidated. Nonconverged solver/KKT heads are rejected, logged, and do not terminate the remaining penalty sweep.
- Identical Log-Bar strings are captured once, but reused for their separate numerical labels in the fitting matrix. This is deliberate and exposes the intrinsic Log-Bar quantization ceiling.

## Prerequisites

Patch is rooted at `main_computer_test/` and adds only:

- `tools/nanojev_two_number_sparse_decoder.py`
- `tools/TWO_NUMBER_FULL_TOKEN_LASSO.md`
- `tests/test_nanojev_two_number_sparse_decoder.py`

Extract `main_computer_test/` contents at `C:\Users\subsi\main_computer`. Keep the current `nanojev_full_activation_sparse_hierarchy.py` **from the latest convergence-fix patch**, plus existing `nanojev_multiscale_activation_decoder.py`, `nanojev_hierarchical_activation_decoder.py`, their supporting scripts, and the NanoJev virtual environment. Requires numpy, scipy, scikit-learn, torch and transformers as for the prior experiment.

## Commands (PowerShell)

**1. Inspect the test plan without loading Qwen:**

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_two_number_sparse_decoder.py --prepare-only
```

**2. Capture the full 2,000-pair run (default).** About 5,919 distinct prompts are expected under the pinned pair generator, because repeated and quantized inputs share activation records. The precise number is printed by `--prepare-only`.

```powershell
& $py .\tools\nanojev_two_number_sparse_decoder.py `
  --device cuda `
  --capture-only `
  --output-root "$env:USERPROFILE\NanoJev\runs\two_number_sparse_v1"
```

**If capture was interrupted**, use the exact same options plus `--resume`. It validates and reuses sealed records:

```powershell
& $py .\tools\nanojev_two_number_sparse_decoder.py `
  --device cuda --capture-only --resume `
  --output-root "$env:USERPROFILE\NanoJev\runs\two_number_sparse_v1"
```

**3. Run the LASSO decoder offline, without Qwen/CUDA:**

```powershell
& $py .\tools\nanojev_two_number_sparse_decoder.py `
  --analyze-only `
  --layers 16,28 `
  --output-root "$env:USERPROFILE\NanoJev\runs\two_number_sparse_v1"
```

For a small initial pilot, choose a **new directory** and supply `--pair-count 200` to the capture command. The default 2,000-pair count is deliberately larger because a two-value decoder is more challenging; 200 is for pipeline smoke testing, not final reconstruction.

To explore weaker penalties later, rerun only analysis with `--alpha-ratios 0.005,0.003,0.0015,0.001`. No recapture required.

## Outputs

- `manifest.json` — fixed pair bank, prompt hashes, encodings, model revision.
- `records/*.npz` — sealed, full token states and token IDs per unique prompt; safe to resume.
- `two_number_fit_curve.csv` — each representation, penalty, stage: both-position pass rate, each position's pass rate, sign accuracy, extreme-contrast pass rate, **swapped-order consistency**, nonzero coefficient count, convergence diagnostics.
- `two_number_predictions.csv` — every measured pair and both independent reconstructions with each position's relative error and pass/fail status.
- `two_number_fit_failures.json` — nonconverged fits (not counted as successes).
- `two_number_summary.json` — progress/results summary, marked explicitly **in sample**.
- `two_number_models/<representation>/alpha_.../` — selected raw feature indices, LASSO coefficients, centering and scaling for each fitted sign/router/regression head.

## Notes on interpretation

The primary success metric is **joint** accuracy. `99%` accuracy for each position does not automatically imply `99%` of pairs pass. A position swap must decode both values when their order is reversed. Error on `x1=2,000,000`, `x2=-3` is calculated independently against each true value, so a small absolute error on `-3` can still fail its 5% threshold.

The raw feature dimension is proportional to **token count × 1,024 × selected layers**. The full source states and all layer positions are retained in capture; the chosen layer selection is an explicit runtime setting (not learned or averaged). This design is more costly in disk, memory and CPU than the earlier pooled-number probes. Saving raw states permits later experiments to change layer selection without re-running Qwen.

The run measures ability to fit the sampled pairs. It does not establish out-of-sample numerical generalization.
