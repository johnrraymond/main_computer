# NanoJev — Constrained 38-head numerical register experiment

**Standalone additive experiment.** The frozen Qwen3-0.6B backbone, existing v3 23,552-prompt capture benchmark, existing `register_online_hierarchy_v1` checkpoints, and all production services remain untouched. The new trainer writes checkpoints only under its new results directory.

## Install and prerequisites

Extract this ZIP **rooted at `main_computer_test/`** into your `C:\Users\subsi\main_computer` repository; copy `main_computer_test/tools/...` to `tools/...` and `main_computer_test/tests/...` to `tests/...` if applying manually. This package adds:

- `tools/nanojev_register_online_hierarchy_constrained.py`
- `tools/REGISTER_ONLINE_HIERARCHY_CONSTRAINED.md`
- `tests/test_nanojev_register_online_hierarchy_constrained.py`

The following previously installed scripts are required but are *not modified*:

- `tools/nanojev_register_online_hierarchy.py` (original 38-head experiment)
- `tools/nanojev_register_online_neural.py` (including its earlier CUDA RNG resume fix)
- `tools/nanojev_register_stability_v3_generate.py`
- `tools/nanojev_numerate_spine_cassette_smoke.py`

The v3 captures under `%USERPROFILE%\NanoJev\runs\register_stability_v3` must already exist. The original `register_online_hierarchy_v1\best.pt` is optional for comparison.

## Exact constraints implemented

The existing 38 supervised outputs are preserved: **2 registers × (zero 1 + sign 1 + thresholds 5 + signed magnitude experts 12)**. The backbone stays completely frozen; its 8,192 captured coordinates and register prompt formatting are unchanged. All training operands come from the original balanced fresh-pair stream; four held-out operation variants and all reserved benchmark operand **pairs** remain excluded. Individual operands can recur, so benchmark generalization is to new pairs, not guaranteed never-before-seen individual integers.

**1. Bounded logarithmic expert reconstruction (`--constraint-stage bounded`)**

- Each band uses exact **integer** endpoints `(1..20), (21..200), (201..2000), (2001..20000), (20001..200000), (200001..2000000)`.
- For magnitude `m` in band `b`, the supervised local target is `t = 2 * (ln(m)-ln(lo_b)) / (ln(hi_b)-ln(lo_b)) - 1`. This is log-normalized for **every band**, including 1–20.
- The independent linear 38-output decoder is retained in this ablation. Out-of-band raw predictions receive a differentiable range penalty in training. Inference **clips** each expert's `t` to `[-1, 1]` and maps it back to `[lo_b, hi_b]`. Raw training outputs are *not* passed through a saturating sigmoid or tanh.

**2. Shared magnitude structure (`--constraint-stage shared`)**

- Replace 24 independent magnitude slopes with **one common magnitude projection per register** and a small low-rank band adapter (default rank 8).
- Couple positive and negative magnitude estimates through an antisymmetric **sign-specific residual**, regularized toward zero. Both signs begin with the same prediction and can diverge only when supervision supports it.
- Zero/sign/five route classifiers still have their independent projections. **38 individual supervised loss components** are retained.
- Trainable parameter count (defaults): **262,376**, versus **311,334** for the old independent 38-head decoder. Sharing is a structural constraint, not just a penalty.

**3. Neighboring expert continuity (`--constraint-stage continuity`, default)**

- Everything in `shared`, plus near-boundary supervision of both neighboring signed experts. An operand counts as near a boundary when `|ln(|n|)-ln(cut)| <= --boundary-window` (default `0.12`).
- On each such example, the true numerical target is clamped separately to each expert's legal interval; the training loss penalizes each neighbor's logarithmic estimate against that target and penalizes discontinuity between their predictions.
- Distant examples give **zero boundary-continuity gradient**. This is training-only auxiliary supervision: at inference, **all routing uses predictions, never true labels**. Oracle routing is reported only for diagnostics.
- The magnitude loss is still masked by each true signed bucket. The all-expert range penalty and sharing regularizer intentionally apply beyond the active expert.

The numeric range bounds apply to expert predictions. Incorrect predicted routing can still select the wrong bounded expert, so the final numerical estimate can be wrong.

## Self-test

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_online_hierarchy_constrained.py --self-test
& $py -m pytest -q .\tests\test_nanojev_register_online_hierarchy_constrained.py
```

## CUDA pilot — fully constrained, 500 steps

```powershell
& $py .\tools\nanojev_register_online_hierarchy_constrained.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_constrained_v1" `
  --baseline-results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_v1" `
  --constraint-stage continuity `
  --steps 500 `
  --batch-size 16 `
  --replay-updates 2 `
  --lr 0.0003 `
  --eval-every 100 `
  --checkpoint-every 100 `
  --report-every 50 `
  --device cuda `
  --dtype float16 `
  --threads 2
```

This generates 8,000 fresh pairs and performs 1,000 decoder gradient updates. Each saved checkpoint includes the model, optimizer, full fresh-pair generator state, benchmark/backbone identity, and CPU/CUDA RNG state. The `--baseline-results-root` argument is optional, but when provided it must contain your previous independent 38-head `best.pt` and `run_config.json`; it is **read-only**. Baseline and constrained models are scored on the same 512 validation captures; the model comparison uses **joint 5%-pass rates**, not uncalibrated training losses across differing target transformations.

## Resume (e.g., to 5,000 total steps)

Run the same exact arguments with `--steps 5000 --resume` in place of `--steps 500`. **Do not change** architecture, rank, penalty coefficients, seed, learning rate, batch size, or other config-sealed options within one run. `--steps` is a total count, not additional steps. `best.pt` holds the best validation checkpoint and `latest.pt` holds the most recent resumable checkpoint. New constrained checkpoints cannot be loaded directly by the older independent 38-head trainer, or vice versa.

## Final evaluation — Qwen inference not needed

After training, repeat the pilot command with `--eval-only` and any positive `--steps` value. Retain all configuration-sealed arguments (`--batch-size`, `--lr`, `--constraint-stage`, etc.). This loads the new `best.pt` and evaluates the **val/test/control** cached v3 activations, including four operation variants excluded from online training. It also compares the old independent baseline on the same splits if `--baseline-results-root` is provided. The training loop does not open test or control until `--eval-only`.

## Controlled ablation: bounded versus shared versus continuity

Use **separate, initially empty results directories** for the three options. To isolate constraints, keep the same `--seed`, batch, optimizer, Qwen, benchmark, and step count for each run:

- `--constraint-stage bounded --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_bounded_v1"`
- `--constraint-stage shared --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_shared_v1"`
- `--constraint-stage continuity --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_continuity_v1"`

The stage is part of the sealed checkpoint configuration. Data generation remains deterministic for a given seed; do not train all stages into one checkpoint directory.

## Metrics and events

- `constrained_decoder_ready`: architecture stage, parameter count, benchmark identity, frozen-backbone status.
- `constrained_train_progress`: fresh pairs, step time, independent backbone-inference and head-update times, mean loss, active supervised heads, boundary examples in the last batch.
- `constrained_validation`: 5% joint and individual value recovery; **all 38** per-head diagnostics; true-bucket expert normalized MAE; sign/route recovery; oracle-routed recovery; number of boundary-near examples; continuous validation loss. If supplied, read-only baseline comparison includes `baseline_joint_pass_rate` and `joint_delta_vs_baseline`.
- `constrained_best_checkpoint`: joint recovery primary; continuous validation loss as the tie breaker. `constrained_checkpoint` is the resumable latest checkpoint.
- `constrained_final_evaluation`: cached validation, held-out numerical pair test, and 24-operation control evaluations. Evaluation-only does not load or modify the Qwen model.

## Testing scope

29 local CPU tests passed across the preexisting and new 38-head suites. They include bound/round-trip exactness, simulated gradients of neighboring experts, no boundary gradients for distant samples, readout coupling, backpropagation, independent-head masking, benchmark exclusion, checkpoint restore/drift, read-only baseline comparison, and simulated train → resume → eval. **The package has not been trained on the user's actual CUDA/Qwen installation, and no numerical recovery improvement is claimed yet.**
