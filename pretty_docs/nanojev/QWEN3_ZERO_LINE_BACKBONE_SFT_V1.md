# Qwen3-0.6B-Base: bounded signed-zero-line backbone training experiment

## Objective and scope

This is the first stage missing from the Qwen HybridAgent path in the 2026-10-25 repository snapshot: teach the **backbone itself** to predict numerical cancellation reasoning and answers. It is not a replay of the HybridLink-only teacher-alignment task.

The target invariant is `A + (-A + δ) = δ`, with positive, negative and exact-zero signed residuals and two-step compositions. Model predictions must generalize across unseen absolute operand magnitudes, not merely repeat a frequent operand.

The training harness changes low-rank `q_proj`/`v_proj` attention weights in Qwen3-0.6B-Base. The underlying pretrained weights, token embeddings and prepared HybridLink v2 head remain frozen. No arithmetic executor, external task router, privileged numerical expert, or answer oracle is used at inference.

## Install and run

Apply the generated `main_computer_test` patch through the repository's `new_patch.py` launcher. Use the NanoJev Python interpreter that already passed HybridAgent smoke checks. Run from `main_computer`:

```text
python -m tools.nanojev_qwen_zero_line_backbone --device cuda
```

The default run is **40 optimizer steps**, one sample per step, rank-8 low-rank attention adaptation, learning rate `2e-4`, maximum sequence length 192, evaluations every 10 steps. Training is bounded, not a numerical certification. The baseline must already exist at `~/NanoJev/runs/qwen3_06b_base_hybridagent_ln_v2` and the pinned `Qwen/Qwen3-0.6B-Base` weights must be cached; the script does not download model files unless `--allow-model-download` is explicitly passed.

Outputs are saved separately under:

`~/NanoJev/runs/qwen3_06b_zero_line_backbone_sft_v1/`

- `run_config.json`: exact training configuration, model revision, baseline hashes, and data fingerprint.
- `best_lora.pt`: low-rank backbone weights chosen by development **thought utility**.
- `last.pt`: low-rank weights, AdamW optimizer, AMP scaler and RNG state to resume.
- `result.json`: training diagnostics, teacher-utility comparison, small blind numerical holdout and GPU allocation. Training completes with `TRAINING_SMOKE_COMPLETED_UNCERTIFIED`, not an arithmetic-success label.

## Deterministic pause/resume

Pause at step 10, even when it is not an ordinary evaluation step:

```text
python -m tools.nanojev_qwen_zero_line_backbone --device cuda --stop-after 10
```

Resume without changing training parameters:

```text
python -m tools.nanojev_qwen_zero_line_backbone --device cuda --resume
```

Do not run the original command twice into an existing directory; accidental overwrite is refused. Use a distinct `--output` for a new experiment. Changing baseline artifacts or training configuration blocks resume.

## What the smoke actually assesses

Training samples are labeled numerical-thought continuations. The prompt itself contains the expression but not the gold answer. The label contains the signed residual and final answer, as legitimate supervised target text.

The development and test magnitude ranges do not overlap the training magnitude range. Every six examples include all combinations of residual sign (negative, zero, positive) and expression family (direct, composed).

The **oracle thought utility** probe holds the expression fixed, and measures gold-answer log probability conditioned on either:

- `The equal-and-opposite terms cancel. Keep the signed remainder.`
- `The equal-and-opposite terms reinforce. Keep the large magnitude.`

Neither thought contains a number or the correct answer. The expected answer is introduced only inside the scorer after conditioning, never in the model's prompt. Utility is a *relative conditional score*, **not** proof that the model computed a residual.

The blind test runs greedy text generation from only the held-out expression and `Reasoning:` cue. Neither the correct numerical thoughts nor the correct answers are provided. The result prints all completions and exact-score correctness for six test cases across signs and expression families. Six cases are only a smoke, not certification.

The first-phase report refuses to call any observed improvement generalized numerical competence. A low or negative utility score is a meaningful negative experimental result, not reason to conceal the output. A training mechanics failure (nonfinite gradient, unexpected frozen-parameter gradient, checkpoint drift or optimizer failure) is a hard refusal.

## Future dependency

The old 80-step head checkpoint was aligned to the **previous** backbone. After any effective backbone training, that head must be re-aligned using the new Qwen backbone-plus-LoRA weights. Do not silently plug the old head into the changed backbone and call it trained recurrent reasoning. Subsequent stages: teacher-thought utility gating, re-alignment of HybridLink to numerically trained Qwen, self-conditioned joint training and much larger OOD cancellation evaluation.

## CPU regression tests

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_zero_line_backbone.py
```

These tests use a small stand-in transformer to exercise real PyTorch gradients, target masking, data segregation, controlled updates, pause/resume and baseline tamper refusal. Live Qwen/CUDA results remain to be established on the user's machine.

## FP16 backward refusal fix (gradient diagnostic and safe first-step restart)

The initial CUDA command could fail with `ZERO_SFT_GRADIENT_MISSING_OR_NONFINITE` before any optimizer step. The former assertion hid whether the cause was disconnected LoRA parameters or nonfinite gradients. CUDA autocast was hardcoded to FP16, and the default initial AMP loss scale was 65,536; scaled FP16 gradients can overflow even with FP32 LoRA parameters.

The fixed trainer chooses **BF16 autocast when the CUDA device supports it**, while keeping model weights, baselines, and optimizer parameters separate and unchanged. When BF16 is unavailable, it uses FP16 with a conservative initial loss scale of **256** and, if a nonfinite gradient occurs, performs up to five full recomputations at halved loss scales before refusing. It never calls `optimizer.step()` on invalid gradients. It reports `ZERO_BACKBONE_GRADIENT_RETRY` for each retry and emits a named `ZERO_SFT_GRADIENT_NONFINITE`, `ZERO_SFT_GRADIENT_MISSING`, or `ZERO_SFT_ALL_GRADIENTS_ZERO` refusal when unrecoverable. These are separate diagnostic outcomes, not a claim that overflow caused the observed failure.

Run exactly as before:

```text
python -m tools.nanojev_qwen_zero_line_backbone --device cuda
```

An earlier **first-step failure** normally leaves only `run_config.json` in the run directory. If and only if this is the directory's sole entry and the complete prior configuration matches, the corrected trainer recognizes the inert pre-step record, updates the AMP-setting metadata and restarts from step zero. It refuses a changed baseline, changed train settings, a partial checkpoint, any other files, or an existing successful run. Do **not** delete checkpoints to bypass that protection.

To force the alternative numerical mode for diagnostics, use `--amp-dtype float16` or `--amp-dtype bfloat16`. The latter refuses if the GPU cannot run BF16. The effective mode is recorded in `run_config.json` and the final result to prevent unsafe resume across modes. The CPU unit tests cover simulated first-step overflow, scaler backoff, missing and nonfinite parameter gradients, no-invalid-step behavior, and safe pre-step recovery. They do not prove which failure mode happened in the original CUDA run; the CUDA rerun must establish that.
