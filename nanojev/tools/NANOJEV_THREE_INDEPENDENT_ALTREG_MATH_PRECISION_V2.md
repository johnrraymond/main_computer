# NanoJev: independent frozen-backbone math-head precision continuations (v2)

This is an **additive patch**. It does **not** replace or modify the v1 trainers, v1 results, or the mature Qwen v3.7/v3.9 lineages. All ZIP entries are rooted at `main_computer_test/`; stage/copy the files into the corresponding `tools/` and `tests/` directories of the working repository.

## Three code-isolated experiments

| Frozen backbone | Standalone trainer file (`tools/`) | Independent default results directory (`%USERPROFILE%\NanoJev\runs\`) | Required same-backbone v1 source |
| --- | --- | --- | --- |
| Qwen3-0.6B | `nanojev_altreg_independent_math_qwen_precision_v2.py` | `independent_altreg_math_qwen_precision_v2` | `independent_altreg_math_qwen_v1` |
| EleutherAI Pythia-70M | `nanojev_altreg_independent_math_pythia_precision_v2.py` | `independent_altreg_math_pythia_precision_v2` | `independent_altreg_math_pythia_v1` |
| TinyStories-33M | `nanojev_altreg_independent_math_tinystories_precision_v2.py` | `independent_altreg_math_tinystories_precision_v2` | `independent_altreg_math_tinystories_v1` |

No v2 trainer imports a sibling trainer or reads its outputs. Each one holds its own model loader, math head, optimizer, data stream, checkpointing, evaluation and precision-loss implementation. Each reads **only its own v1 best.pt and validation-cache.pt, once at fork**, then carries an independent copy of the checkpoint state and held-out captures. `torch.load(..., weights_only=True)` and full model/config/data identity checks reject mismatches.

### Forking and resumption

- New v2 runs fork from the same-backbone `v1/best.pt`, **not** v1 `latest.pt`. They restore head parameters, all three optimizer states, Python training RNG and PyTorch/CUDA RNG, stream position and 16:3:1 phase counts. If the v1 best checkpoint is unavailable, the fork fails; **it does not silently start from scratch**.
- The first v2 step is v1 best step plus 1. `--steps 10000` means **10,000 additional v2 optimizer updates** after the fork, not an absolute step number. A TinyStories v1 best at 10,000 therefore yields v2 step 20,000 on completion.
- A v2 `--resume --additional-steps N` continues exactly from v2 `latest.pt`, without opening the v1 run. A v2 source-root is never overwritten. Different model identities and training parameters fail closed.
- Each v2 results directory includes `fork.pt`, `latest.pt`, `best-asinh.pt`, `best-precision.pt`, `baseline.json`, `fork.json`, `validation-cache.pt`, step-wise validation JSON, and `test-last.json`.

## Numerical-precision loss (INTERMEDIATE updates only)

The original X/Y coordinate target is `z = asinh(n)/asinh(MAX_VALUE)`. At each INTERMEDIATE update, the loss is:

```
coordinate_loss = mse(predicted_z, target_z)
precision_loss = smooth_l1(
    (predicted_z - target_z) / coordinate_tolerance(n, 0.05),
    zeros_like(target_z), beta=1.0)
loss = coordinate_loss + 0.01 * precision_loss
```

For each numeric truth `n`, `coordinate_tolerance` is computed analytically as the smaller signed-asinh distance between `n` and `n ± 0.05*max(abs(n),1)`, divided by `asinh(MAX_VALUE)`. Thus a coordinate error less than this conservative tolerance guarantees decoded absolute numerical error at most `0.05*max(abs(n),1)`. The numerator remains in transformed space; this **does not** backpropagate through the exponential inverse `sinh()`. Smooth L1 produces finite, bounded derivatives before the user-selected weighting. Gradient finite checks and inherited norm clipping remain active.

The default coefficient `--precision-weight 0.01` is an experimental starting value, **not** a verified optimum. `--precision-weight 0` gives a matched old-loss continuation if started in a **different** results root; do not change it mid-run.

`RESULT` (15%) and `FINAL` (5%) still use exactly their v1 MSE objectives, each on original-order prompts; `INTERMEDIATE` (80%) alone alternates first X/then Y vs first Y/then X, always preserving label-to-value assignments. The frozen backbone and head architecture are unchanged. All three trainers use the same deterministic problem generation and held-out selection.

### Expanded diagnostics

Per original, reversed, and decoded-average mode: X/Y 5%, 1%, 0.1% accuracy; joint 5%; X/Y transformed signed-asinh MSE; X/Y decoded relative numerical MSE; RESULT/FINAL transformed error and 5% accuracy. The transformed objective selects `best-asinh.pt`. An **independent numeric priority** ranks by paired 5%, 1%, 0.1% operand accuracy, with relative decoded error only breaking ties; it selects `best-precision.pt`. Both start from the v1 fork baseline, so the starting checkpoint remains eligible if no continuation improves it.

## Offline checks

From `C:\Users\subsi\main_computer` (or equivalent repo), with the NanoJev Python environment:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_altreg_independent_math_qwen_precision_v2.py --self-test
& $py .\tools\nanojev_altreg_independent_math_pythia_precision_v2.py --self-test
& $py .\tools\nanojev_altreg_independent_math_tinystories_precision_v2.py --self-test

& $py -m pytest -q .\tests\test_nanojev_altreg_independent_math_qwen_precision_v2.py .\tests\test_nanojev_altreg_independent_math_pythia_precision_v2.py .\tests\test_nanojev_altreg_independent_math_tinystories_precision_v2.py
```

## Independent 10,000-update continuations

**Keep `--val-examples` and `--test-examples` consistent with each respective v1 run**. The TinyStories v1 command used 128 each, as shown here. If Qwen/Pythia used different counts, replace 128 accordingly. Also keep the v1 seed, LR, batch size, head width and max-prompt-tokens; the defaults match the original v1 scripts. Each v1 run must have completed at least one validation so that `best.pt` and `validation-cache.pt` exist.

TinyStories:

```powershell
& $py .\tools\nanojev_altreg_independent_math_tinystories_precision_v2.py `
  --device cuda --dtype float16 `
  --steps 10000 --val-examples 128 --test-examples 128 `
  --eval-every 100 --report-every 50 --allow-model-download
```

Qwen (after its independent v1 run has produced `best.pt`):

```powershell
& $py .\tools\nanojev_altreg_independent_math_qwen_precision_v2.py `
  --device cuda --dtype float16 `
  --steps 10000 --val-examples 128 --test-examples 128 `
  --eval-every 100 --report-every 50 --allow-model-download
```

Pythia (after its independent v1 run has produced `best.pt`):

```powershell
& $py .\tools\nanojev_altreg_independent_math_pythia_precision_v2.py `
  --device cuda --dtype float16 `
  --steps 10000 --val-examples 128 --test-examples 128 `
  --eval-every 100 --report-every 50 --allow-model-download
```

Do **not** start these three simultaneously on a GPU that cannot hold multiple backbones. Each script is independent; run sequentially if memory is limited.

Resume example (TinyStories v2, another 1,000 optimizer updates from its v2 `latest.pt`):

```powershell
& $py .\tools\nanojev_altreg_independent_math_tinystories_precision_v2.py `
  --device cuda --dtype float16 --resume --additional-steps 1000 `
  --val-examples 128 --test-examples 128 `
  --eval-every 100 --report-every 50 --allow-model-download
```

### Evaluate different checkpoints independently

Use `--eval-only --eval-checkpoint latest|fork|best-asinh|best-precision`; for example:

```powershell
& $py .\tools\nanojev_altreg_independent_math_tinystories_precision_v2.py `
  --device cuda --dtype float16 --eval-only --eval-checkpoint best-precision `
  --val-examples 128 --test-examples 128
```

This evaluates cached frozen backbone activations on held-out validation **and test** examples, without training or loading a live backbone. For a matched zero-precision-weight control, run the **same** trainer with `--precision-weight 0` and `--results-root` pointing to a new directory; do not overwrite an existing v2 lineage.

## Verification scope

Local validation used CPU mock frozen models because the test container does not include the workstation's CUDA setup. Tests exercised the **actual v1 → v2 fork**, state/optimizer/stream preservation, changed X/Y loss, gradient finiteness, analytical 5%-coordinate tolerance, prompt order by phase, validation, both best-checkpoint outputs, restart, evaluation of all four checkpoint types, wrong-backbone rejection, and original v1 integration compatibility. Run the above CUDA commands on the Windows workstation to validate real model execution.
