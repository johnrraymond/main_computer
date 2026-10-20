# NanoJev T02: Continuous Numerical Heads Benchmark V1

## Objective

Determine whether T02's roughly 20–25% held-out X success arises chiefly from the existing V3 sign / discrete-exponent / sigmoid log-fraction readout, or from the frozen intermediate representation used by that readout. Preserve the current NanoJev decoder, Qwen backbone, scientific teacher database, masks, split assignments, and source checkpoint. Never write to the original checkpoint directory or alter the training source.

T02 requests `X = a*HAWK_HAWK_PAYOFF + b*HAWK_DOVE_PAYOFF` and supplies Y on the opposite branch. With the current verified fixture this simplifies to `X = 3b - a` and Y to `1.5b`, but **those coefficients are not given to any trainable head**. The benchmark obtains ground truth solely from the existing teacher database.

## Implemented test arms

| Arm | Inputs | Prediction and training | Question answered |
|---|---|---|---|
| `original_V3` | Frozen trained V3 trunk, same T02 prompts | Original checkpoint's sign/exponent/mantissa heads, no modification | Fixed reference |
| `direct` | Frozen trained V3 penultimate body features (X,Y slots) | Direct `X/1e6, Y/1e6`, unconstrained output, smooth-L1 | Is output factorization the immediate obstruction? |
| `symlog` | **Same features** | Continuous signed `sign(x) log(1+abs(x)/1e5)` with inverse, smooth-L1 | Does continuous signed compression help? |
| `twohot` | **Same features** | Two-hot soft labels on 257 signed-log bins, cross entropy; expected-bin inverse | Is distributional numerical prediction better than scalar regression? |
| `operands` | T02's two exact counts extracted and validated from prompt, `a/1e6, b/1e6` | Plain affine 2-to-2 readout fitted by least squares on training rows only; no coefficients supplied | Can the problem be solved when precise numerical inputs are directly accessible? |

The three numerical readouts have the same small nonlinear head (LayerNorm → 96 hidden GELU → output) over **the identical frozen penultimate features**, so an improvement can be attributed more narrowly to the output coordinate and loss than to a different backbone representation. All three start from matched deterministic seeds and train for the same fixed number of epochs on exactly the same T02 train examples. The explicit-operand control instead uses deterministic least squares on the training split, preventing optimizer nonconvergence from invalidating the reference. It is a diagnostic, not a fourth architecture competing under identical conditions.

**Interpretation limit:** The pretrained V3 penultimate features are themselves already a potentially lossy transformation of frozen Qwen states. If these heads fail while the explicit-operand control succeeds, that does **not** isolate Qwen as the source of loss. A second stage would need to expose earlier token features or fine-tune the decoder trunk. This first experiment does not test fine-tuning Qwen itself.

## Data and integrity

- Teacher: existing `runtime/hawk-dove-million/teacher-scientific-v2.sqlite3`, **not rebuilt**.
- Donor: existing saved `NanoJev/runs/hawk_dove_million_scientific_v2/latest.pt`, **not modified**. Stop any process still training this donor, because its byte hash is pinned during the experiment.
- `prepare` captures trained V3 pre-readout features **once** using the local frozen Qwen backbone, plus original V3 numerical predictions on matching examples. No gradients through Qwen or pretrained V3 trunk.
- Split: existing 165 train / 23 dev / 13 test instances, 8 masks per instance. The benchmark refuses missing or unbalanced mask sets. The default `All` command never opens the test split.
- Inputs for the operand control come from parsing the `MUL <count> HAWK_*_PAYOFF` nodes after validating any LogBar/scientific annotations; it does not read hidden targets or payoff fixture bindings to construct input features.
- Feature archives and trained readout checkpoints are independently SHA-256 checked and bound to source checkpoint, code, and teacher identities. An existing frozen dataset/model is reused; there is no destructive overwrite.
- Numerical metrics: identical joint/X/Y within-5%-relative gates for every arm; also MAE/RMSE and a fixed **absolute 5,000-unit** tolerance for small and zero outputs; report includes all eight masks.
- No dev-based early stopping or hyperparameter sweep is applied in V1. Train all arms for the same fixed epoch count, score train and dev. Run held-out test **only after** explicitly selecting a single winner from dev.

## Installation

Unzip into the repository root with the existing `main_computer_test/` patch convention. This patch adds one Python module, one PowerShell runner, one test file, and this guide; it does not overwrite the existing NanoJev trainer, decoder, or teacher files.

In PowerShell from `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_hawk_dove_continuous_benchmark -v
```

## All-stage command (recommended)

```powershell
& .\tools\run-nanojev-hawk-dove-continuous-benchmark.ps1 `
  -Stage All `
  -Python $py `
  -Device cuda `
  -TrainingDevice cpu `
  -Epochs 120 `
  -CaptureBatchSize 4
```

`Device cuda` runs frozen Qwen capture on the RTX 3060; CPU training is intentional because these readouts are tiny. `All` first captures train and dev features, then fits all four heads independently, then prints the full train/dev scores. It does not use the running 6-million-step trainer and does not reopen the test split. Since feature capture is much smaller than millions of online Qwen passes, there is no need to resume or interfere with the training run.

## Separate stages / interrupted runs

```powershell
& .\tools\run-nanojev-hawk-dove-continuous-benchmark.ps1 -Stage Prepare -Python $py
& .\tools\run-nanojev-hawk-dove-continuous-benchmark.ps1 -Stage Train -Python $py -Epochs 120
& .\tools\run-nanojev-hawk-dove-continuous-benchmark.ps1 -Stage Probe -Python $py
```

`Prepare` creates `features-train.npz` and `features-dev.npz`. Subsequent stages verify their hashes. The frozen feature extraction is cached in those immutable files. Method models are also retained and verified on rerun. A new experiment with a different donor or training schedule should use a new `-RunRoot` rather than editing this frozen experiment.

Output folder: `C:\Users\subsi\NanoJev\runs\hawk_dove_t02_continuous_benchmark_v1`

- `results-train.json` / `results-dev.json`: accuracy and errors by method and mask.
- `direct.pt`, `symlog.pt`, `twohot.pt`, `operands.pt`: independent readout checkpoints.
- `features-train.npz` / `features-dev.npz`: immutable per-case extracted features and numeric targets, plus metadata and audit hashes.
- `manifest.json`: pinned checkpoint/data/source/code identities.

## One-time final test (only after evaluating dev)

If `symlog` wins on the dev set, for example:

```powershell
& .\tools\run-nanojev-hawk-dove-continuous-benchmark.ps1 `
  -Stage Test -Python $py -Winner symlog
```

This seals the selected method **before** opening test rows and evaluates only that method against the unmodified V3 baseline. It refuses to change the winner or retest with another method. Do not use `-Stage Test` until the dev results have been reviewed.

## Interpretation

- Direct beats original V3 on X with comparable train/dev gains: evidence the old output decomposition or its optimization was restricting accuracy.
- Symlog > direct: continuous signed compression helps; no discontinuous mantissa target is needed.
- Twohot > both scalar heads: distributional target supervision helps even with the same features.
- Explicit operands ≫ three feature heads: the arithmetic is straightforward with correct inputs, but the frozen *pre-readout* representation/readout family does not transmit or extract the needed numerical information sufficiently; determine whether loss occurs in Qwen, parent, attention, or body in the next stage.
- High train and low dev for any readout: fitting without generalization, not proof of a solved arithmetic mechanism.
- All three frozen-feature readouts fail: **do not** immediately claim direct regression failed generally. The experiment freezes the pretrained body; follow with a fully trainable-body or token-feature probe before drawing that conclusion.

This is an experimental setup, not a claim of CUDA-validated improvement. The hardware run and its measured results remain outstanding.
