# NanoJev HybridAgent v2 — contextual reasoning experiment

## Purpose and invariant

The frozen 2,000-bank Gaussian source remains the exact **zero-thinking-step** predictor. A **new** causal numerical reasoning network accepts the visible problem context, appends each self-generated 128D latent vector to a growing sequence, and produces a new hidden representation decoded by the **same frozen Gaussian density head**. The causal reasoning core, residual HybridLink, auxiliary stage probe and Gaussian hidden-state readout are jointly optimized during the self-conditioned stage. Existing v1 trainer, probe, and original checkpoint remain unchanged.

This is a small numerical analogue of HybridAgent, **not** a direct port of its Qwen transformer / KV cache, token-aligned intermediate reasoning, or HybridRL mode policy. It uses a new small trainable transformer instead of unfreezing the original reference model; retaining the reference is deliberate to preserve exact zero-step parity. The synthetic intermediate teacher embeddings use the source input layer but are not necessarily in the manifold of actual natural-language tokens. These are experimental choices and must not be presented as verified equivalence to HybridAgent.

## Changes relative to the failed v1 experiment

1. **Full causal memory:** context and every generated input vector remain as tokens in the same prefix; the reasoning network attends causally to the entire preceding sequence. It does not repeatedly map only the last vector back into a frozen layer.
2. **Residual HybridLink:** `next_input = hidden + MLP(LayerNorm(hidden))`. Positional input vectors give different steps distinct context.
3. **Ordered teacher:** 8 supervision stages follow four visible count×payoff products, X sum, Y sum, and both signs. Training-only stage-specific vectors are encoded through the frozen source's input projection; never read or injected at inference.
4. **Self-conditioned training:** a teacher-conditioned pass predicts next input vectors and a separate fully autoregressive pass consumes *only model-generated* tokens. The latter unrolls differentiably, jointly training the new reasoning core, residual HybridLink, hidden readout and arithmetic probe using Gaussian numerical losses and intermediate supervision. The frozen original Gaussian source is never modified.
5. **Controls:** zero depth and feedback `disabled` reproduce the reference exactly. `zero_tokens` preserves memory length while removing token content; `shuffled` swaps examples' feedback; `frozen_first` repeats the first generated vector. These controls distinguish real content effects from mere changes in computation depth.
6. **Trajectory evidence:** `report.json` includes `step_evolution` with hidden-state change, latent norm, X/Y shift, error and number of improving/worsening coordinates for depths 1–8.

## Install

Apply `nanojev_gaussian_hybrid_agent_v2_contextual_patch.zip` from the Main Computer repository root:

```text
python new_patch.py nanojev_gaussian_hybrid_agent_v2_contextual_patch.zip --dry-run
python new_patch.py nanojev_gaussian_hybrid_agent_v2_contextual_patch.zip
```

## Train

The new experiment requires the existing v1 `tools.nanojev_hawk_dove_gaussian_hybrid_agent` and its original Gaussian dependencies. It is an independent run. Do not use `--resume` against v1 output folders.

```text
python -m tools.nanojev_hawk_dove_gaussian_hybrid_agent_v2 --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v2" --device cuda --warmup-steps 2000 --steps 12000 --probe-every 500 --batch-size 64
```

To pause without changing the planned curriculum, add `--stop-after 6000`; resume with the **same original** `--steps 12000` and `--resume`. To pause warm-up, use `--warmup-stop-after 1000`, then the same `--resume` command. Checkpoints contain the trainable network, both optimizer states, PyTorch CPU/CUDA RNG, sampling RNG, and DEV selection history.

## Acceptance and failure interpretation

- `HYBRID_V2_ZERO_STEP_PARITY=PASS` establishes exact source prediction parity at depth 0.
- `HYBRID_V2_ACTIVATION_GATE=PASS` establishes a decrease in ordered-stage alignment error. It **does not** prove improved numerical reasoning.
- `HYBRID_V2_SELF_CONDITIONING=ACTIVE` establishes generation/consumption of the model's own vectors during training.
- `NO_CAUSAL_LATENT_GAIN_ESTABLISHED` is a scientifically valid result if added steps do not beat both the zero-step reference and latent-ablation controls. The final status does **not** assert success just because training completed.
- Inspect `DEV`, `DEV_disabled`, `DEV_zero_tokens`, `DEV_shuffled`, `DEV_frozen_first`, and `step_evolution` in the final `report.json`. The final report also evaluates TEST only after selecting the checkpoint on DEV.

Source payoff-matrix splits are reconstructed from the original checkpoint and strictly verified. Historical DEV and TEST have already been examined, so these are **exploratory comparisons**, not fresh blind certification. New matrices are required for the ultimate 80% per-tier gate.

## Scope

No learned THINK/ANSWER stopping policy (Phase 3), no Qwen fine-tuning, no dynamically reallocated budget, and no claim that more iterations necessarily help. This patch changes the missing causal-memory and joint-training mechanisms without overwriting or silently mutating any existing experiments.
