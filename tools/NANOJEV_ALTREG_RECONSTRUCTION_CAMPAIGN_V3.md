# Independent AltReg — reconstruction audit and decoder campaign v3

## Purpose

Run the **entire set of numerical reconstruction experiments in one code patch**, while keeping original v1/v2 and TinyStories mantissa experiments untouched. The repository snapshot already has `nanojev_independent_math_numeric_audit.py`; this patch **extends** the analysis with paired precision-vs-control comparisons and adds three decoder-v3 pilot arms.

The original Qwen evidence was: Qwen v2 `precision_weight=.01` best 5%-tolerance accuracy 7.62%, vs 5.27% for matched `precision_weight=0`; best normalized signed-asinh scores 0.01116 and 0.01098 respectively. These are **validation** results. The new experiments **do not assume** any arm will meet its gate.

### New files

- `tools/nanojev_altreg_math_reconstruction_compare_v3.py`: comparison of two **actual** precision-v2 directories (default `..._precision_v2` and `..._weight0_control`). Loads each `best-precision.pt` and `best-asinh.pt`, checks the shared v1 parent SHA-256 and the exact held-out feature tensors, makes one combined `predictions.csv` and `comparison.json`. Reports errors by operation/family, sign/exponent/mantissa single and combined oracle repairs, operand swaps, RESULT/FINAL, and paired bootstrap intervals. **No frozen backbone required**.
- `tools/nanojev_altreg_math_v3_decoder_experiment.py`: v3 train/evaluate/resume for `qwen`, `tinystories`, `pythia`. One frozen same-backbone v2 parent, one new trainable decoder per arm. Each arm is separate and immutable from its parent.
  - `residual`: learned direct X/Y correction in normalized asinh space; starts as **exactly zero residual** to inherit the parent prediction; optional original precision penalty `.01`.
  - `factorized`: predicts each X/Y sign class, decimal exponent class, and fractional logarithmic mantissa from frozen parent features.
  - `attention`: same factorized decoder, but adds separately queryable token-level representations for X and Y, from the frozen middle and final layers. No pretrained weights altered. Validates that flattened last-four captured features reproduce v2's holdout cache.
- `tools/nanojev_altreg_math_v3_pilot_compare.py`: validates all arms share an identical parent SHA, seed, batch size, learning rate, and **training endpoint and example count** (the independently selected best checkpoints may occur at different steps). Evaluates their saved validation-best heads on identical data; writes per-example CSV, JSON, and paired case-level bootstrap intervals.
- `tools/nanojev_altreg_math_campaign_v3.py`: launcher for `preflight`, `diagnose`, `pilot`, `compare`, `all`, `final-test`. Executes GPU jobs **sequentially** and propagates failing exit codes. `--dry-run` prints exact commands without modifying files.
- `tests/test_nanojev_altreg_math_reconstruction_campaign_v3.py`: CPU-only model stub tests for fork, training, all arms, held-out attention capture, resume, comparisons, oracle repairs, and fail-closed identity handling.

All ZIP entries are rooted at `main_computer_test/`. Extract/merge into `C:\Users\subsi\main_computer`, **not** over `tools/` as a newly nested directory. No existing files are overwritten by this patch.

## Prerequisites

Install the patch into the currently tested `main_computer` tree. Use your NanoJev interpreter:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
```

The default first experiment is **Qwen**, because it already has both source v2 lineages:

```text
%USERPROFILE%\NanoJev\runs\independent_altreg_math_qwen_precision_v2\best-precision.pt
%USERPROFILE%\NanoJev\runs\independent_altreg_math_qwen_weight0_control\best-precision.pt
```

`validation-cache.pt` is required in each directory, and the parent v2 `best-precision.pt` must be intact. Pythia and TinyStories can use the same v3 pilot, but **a matching own-backbone v2 checkpoint is required**. The paired v2 diagnosis additionally requires their own completed weight-zero control; **no Qwen/TinyStories/Pythia checkpoint is substituted for another**.

## Start the entire Qwen campaign

Preflight:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage preflight --backbones qwen
```

List commands without running:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage all --backbones qwen --dry-run
```

**Run** the full validation-only campaign: paired checkpoint audit, three independently trained v3 decoders for 2,000 updates each, then matched comparison:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py `
  --stage all --backbones qwen --device cuda --dtype float16 `
  --steps 2000 --batch-size 16 --eval-every 100 --report-every 50 `
  --allow-model-download
```

Run a stage alone, if needed:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage diagnose --backbones qwen --device cpu
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage pilot --backbones qwen --device cuda --allow-model-download
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage compare --backbones qwen --device cpu
```

**Do not rerun a completed `--stage all` on the same output directories**. For longer v3 training, use `--stage pilot --resume --additional-steps 1000`, then `--stage compare`. The latest state includes stream and RNG states for continuation; the source parent remains frozen.

Pythia and TinyStories, once their own parents and optional controls exist:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage preflight --backbones tinystories pythia
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage pilot --backbones tinystories pythia --device cuda --allow-model-download
```

These experiments are independently initialized, have independent training streams and heads, and only share read-only tool *code*. Training data are the same between **arms for one backbone**, not between different backbones.

## Evaluation, selection, and test discipline

During training, only the **validation** split selects `best.pt`. `latest.pt`, `best.pt`, `fork.json` and step-indexed validation JSONs are kept in each v3 output root. The 5%, 1%, 0.1%, joint-5%, original/reversed/averaged, asinh MSE and parent-reference metrics are available separately.

To evaluate a single arm without retraining or reloading the backbone:

```powershell
& $py .\tools\nanojev_altreg_math_v3_decoder_experiment.py `
  --backbone qwen --arm attention --device cuda `
  --eval-only --eval-checkpoint best --eval-split validation --save-eval
```

**After** selecting a candidate on validation, perform a **one-time final test** of all arms. This explicit command prevents accidental test-set use during normal candidate development:

```powershell
& $py .\tools\nanojev_altreg_math_campaign_v3.py --stage final-test --backbones qwen --device cpu
```

The v2 comparison also accepts `--split test`, but do not use the test split to select hyperparameters.

### Evaluation gates

- Individual X/Y values within 5%: **20% pilot target** (previous Qwen best 7.62%).
- Individual X/Y within 1%: **5% pilot target** (previous best 1.95%).
- Joint X/Y within 5%: **5% pilot target** (near zero currently).
- No loss of same-example label association, and no unexplained regression in reversed-order performance.

These are experimental advancement targets and **not claims of achieved accuracy**. The `attention` arm is more computationally expensive: it re-extracts complete hidden token sequences and caches validation/test token features in its **own** result directory, without modifying the v2 cache. All training retains frozen backbones. The RESULT and FINAL parent heads remain frozen for this pilot, so RESULT improvements require a later explicitly trained follow-on stage after reliable X/Y recovery.

## Local validation

```powershell
& $py .\tools\nanojev_altreg_math_v3_decoder_experiment.py --self-test
& $py -m pytest -q .\tests\test_nanojev_altreg_math_reconstruction_campaign_v3.py
```

The self-test and toy experiments do not load models from the internet. Full CUDA runtime/training on Qwen, TinyStories and Pythia must be executed on your Windows workstation with your pretrained models and actual `best.pt` files.
