# Qwen3-0.6B HybridAgent pretraining smoke (paper-aligned v2)

This is **one fast, fail-closed smoke**, not a numerical training or certification run. It does no optimizer steps and performs no checkpoint mutations.

## One-time setup and one-command smoke

```text
python -m tools.nanojev_qwen_hybridagent prepare --device cuda --allow-model-download
python -m tools.nanojev_qwen_hybridagent_training_smoke --device cuda
```

The first command prepares a **new** v2 Base checkpoint, never overwriting the earlier v1 instruct checkpoint. The second reads it and emits `HYBRID_TRAINING_SMOKE` JSON with each independent assertion. `status=FAIL` and a nonzero exit code means stop.

### Fail-closed assertions

- `head_architecture`: exact released `pre_ln`, `proj1`, `act`, `proj2`, `post_ln` modules; their execution order and residual formula; finite dimensions.
- `checkpoint_config`, `live_model_and_adapter`, `adapter_exact_restoration`: schema/version, backbone/tokenizer pinning, strict head and marker restoration.
- `paper_base_provenance`: rejects a non-Base starting checkpoint except with explicit `--allow-instruct-backbone`.
- `latent_token_boundary`: distinct, atomic, noncolliding entry/exit marker tokens and embeddings.
- `recurrent_gradient`: cached two-step Qwen feedback, text-only parity, finite adapter gradients.
- `latent_causal_influence`: reversible head perturbation must change a downstream Qwen hidden state.
- `untrained_status_guard`: refuses accidental claim that the initialized head is trained.

`numerical_accuracy`, `oracle_blindness`, `adaptive_switching_accuracy`, and `single_gpu_training_capacity` remain `NOT_TESTED`. Passing mechanical checks cannot establish these learning-dependent properties.

### Compatibility and safety

The prior v1 checkpoint (`qwen3_06b_hybridagent_v1`) stays unchanged; its schema and parameter names are incompatible with v2 by design. Use the new default output. **Do not run `prepare` twice on the same output**; the command refuses overwriting existing outputs.

The new default model is `Qwen/Qwen3-0.6B-Base`; it may require a one-time ~1.2 GB weight download. The smoke never downloads automatically. Use the exact same `--model`, `--revision` (if overridden), and `--output` arguments for both preparation and smoke.

If deliberately creating a *new* normalized instruct checkpoint, pass `--model Qwen/Qwen3-0.6B` and a distinct `--output` to both commands, then `--allow-instruct-backbone` on the smoke. This is a separate experimental arm, not a paper-faithful Base provenance pass.
