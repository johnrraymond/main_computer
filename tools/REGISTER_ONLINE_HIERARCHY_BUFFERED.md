# NanoJev — Buffered / vectorized 38-head constrained register trainer

**Additive experiment; standalone.** This training path never edits the original 38-head program or constrained-v2 program, the existing v3 captures, any original checkpoint, Qwen weights, or production services. This patch adds `tools/nanojev_register_online_hierarchy_buffered.py` and its tests/documentation; it requires your previously installed `nanojev_register_online_hierarchy_constrained.py`, `nanojev_register_online_hierarchy.py`, `nanojev_register_online_neural.py`, v3 generator and cassette utilities.

## Design

- Qwen3-0.6B frozen, unchanged 8,192 activation coordinates (layers 16/28, four suffix token positions), unchanged XX declaration prompts and fresh two-register procedural generator.
- Same 38 supervised outputs: `2 × (zero + sign + 5 ordered threshold heads + 12 signed magnitude experts)`.
- Same shared, log-normalized, hard-bounded expert reconstruction as constrained v2. `--constraint-stage continuity` uses the same shared architecture; the near-boundary term is independently adjustable with `--continuity-weight` and **defaults to zero** so it can be ablated. To reproduce the old continuity regularizer weight use `--continuity-weight 0.1`.
- Replace the old 38 separate Python `mask.any()` branches with a **single GPU-vectorized set of BCE and Smooth-L1 losses** using fixed masks and `one_hot` expert eligibility. Each head has its own masked mean. No `.item()` / `.cpu()` in the per-head loss or backward pass. Relative expert loss weight is adjustable; default `--expert-weight 2.0`.
- The sampler creates fresh pairs as before and stores their **frozen activation vectors as CPU float16** in a bounded FIFO bank for each of 13 categories **per register** (zero + 12 signed magnitude regimes). Records shared between both banks store only one vector. Bank sampling uses replacement in under-populated buckets during warmup; never synthesizes activations or unseen labels. With `--per-expert-batch 16`, one replay update selects up to 26 × 16 = **416** actual, potentially repeated, stored samples. Those samples supervise both registers' classifications and *true-bucket* magnitude experts. It is an optimizer minibatch assembled across buckets, not 416 new Qwen prompts.
- More training updates can be added per fresh batch (`--replay-updates`). Only one frozen Qwen inference per **new** prompt. Data generation excludes all benchmark ordered pairs and four reserved operation variants. Individual integers may recur in new pairs.
- Per-step numeric targets are built on CPU before the single GPU copy, eliminating the previous GPU-band → CPU NumPy → GPU-local-target roundtrip.
- Logging supplies `bank_counts`, active classifier/expert counts, `train_components` (zero/sign/route/expert/range/sharing/continuity), fresh-pair count, a **recent unique replay training probe** with per-expert errors, and a fixed held-out validation score with per-expert errors. The replay probe is **training data**, not generalization evidence. The v3 validation, test and 24-operation control sets remain untouched; `--eval-only` evaluates best checkpoint against val/test/control.
- Checkpoints save **model, optimizer, stream, CPU buffer records, bank membership, bank sampling RNG, and torch/CUDA RNG**. Resume reuses all of them. Saved CPU replay state adds disk size: at full default capacities it can be several dozen megabytes. GPU throughput and learning have **not** been verified in this environment.

## Installation

Extract the ZIP rooted at `main_computer_test/`. Copy its `tools` and `tests` files to the corresponding repository directories (do **not** create a nested `main_computer_test` inside the repository).

## Verify locally

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_online_hierarchy_buffered.py --self-test
& $py -m pytest -q .\tests\test_nanojev_register_online_hierarchy_buffered.py
```

## First CUDA run — initialize from constrained v2 step-400 best model

This copies only the **weights** from the previous constrained checkpoint; it starts a **new** optimizer, fresh-pair stream, replay banks, and step counter in a new directory. It does **not** assert that the previous optimization history is continued.

```powershell
& $py .\tools\nanojev_register_online_hierarchy_buffered.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1" `
  --init-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_constrained_v1\best.pt" `
  --baseline-results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_v1" `
  --constraint-stage continuity `
  --continuity-weight 0 `
  --batch-size 16 `
  --buffer-capacity 256 `
  --per-expert-batch 16 `
  --expert-weight 2 `
  --replay-updates 2 `
  --steps 500 `
  --eval-every 100 `
  --checkpoint-every 100 `
  --report-every 50 `
  --lr 0.0003 `
  --device cuda `
  --dtype float16 `
  --threads 2
```

The original best checkpoint was at step 400. The `--init-checkpoint` option verifies backbone identity, stage, rank, and residual scale before loading. With that option, this is a new **500-step** run rather than steps 401–900 of the old optimizer.

**To start from fresh random weights instead:** omit `--init-checkpoint` and use a different empty results directory.

## Resume and full held-out evaluation

For a true continuation of this buffered run, repeat **the entire preceding command** unchanged except set `--steps 5000` and append `--resume`. Preserve the same `--init-checkpoint` argument (it is stored in the run configuration); on resume it is not loaded a second time.

For held-out evaluations, repeat the preceding command with `--eval-only` (it loads the buffered run's `best.pt` and never loads Qwen). `--eval-only` checks val, test, and control. Test/control should not be used to pick further hyperparameters.

## Interpretation

The previous constrained-v2 run ended with zero joint 5% recovery. This patch addresses minibatch sparsity and GPU synchronization, **not** a demonstrated improvement in numeric readout. Log-scale expert MAE and 5% recovery must both be checked. A decrease in training-probe loss without validation improvement indicates insufficient generalization. Comparing `replay_probe_per_expert` to `heads` in `buffered_validation` helps identify which signed bucket has diverged.

`frozen_qwen_seconds` and `decoder_update_seconds` are approximate Python dispatch times: CUDA work is asynchronous and synchronization occurs at report boundaries. Use wall-clock `elapsed_seconds` and `fresh_pairs_per_second` for reliable total throughput comparisons.
