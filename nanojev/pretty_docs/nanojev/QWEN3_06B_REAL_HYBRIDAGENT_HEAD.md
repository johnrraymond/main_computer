# Qwen3-0.6B-Base with released HybridAgent HybridLink: v2

## Why this patch exists

The v1 implementation passed a real live GPU recurrence smoke but failed paper-implementation conformity. The upstream released implementation (`hybrid_agent/hybridlink.py`) is:

```python
self.proj1 = nn.Linear(hidden_size, hidden_size)
self.act = nn.GELU()
self.proj2 = nn.Linear(hidden_size, hidden_size)
self.pre_ln = nn.LayerNorm(hidden_size)
self.post_ln = nn.LayerNorm(hidden_size)

def forward(self, h):
    return self.post_ln(h + self.proj2(self.act(self.proj1(self.pre_ln(h)))))
```

The v1 residual-only implementation missed both norms and used different names. The new v2 code matches the upstream module names and forward function; the initial weights are freshly initialized. The upstream released checkpoints are 8B/14B and cannot supply trained 0.6B head weights.

## Non-destructive versioning

- New default pretrained backbone: `Qwen/Qwen3-0.6B-Base` at pinned revision `311c62e88814bff7206909ccd330bab0a784743b`.
- Prior instruct backbone remains opt-in via `--model Qwen/Qwen3-0.6B`; it is still a deliberate deviation from the paper's Base starting point.
- New default output: `~/NanoJev/runs/qwen3_06b_base_hybridagent_ln_v2`.
- New schema: `nanojev.qwen3-0.6b.hybridagent-ln-res-link.v2` with `adapter_type=ln_res_adapter`.
- Original `~/NanoJev/runs/qwen3_06b_hybridagent_v1` remains untouched. V1 and V2 adapter states are intentionally *not* implicitly converted or loaded across architectures. Back up the v1 code if reproducing the old experimental arm.
- The weights of the Base backbone are downloaded only with explicit `--allow-model-download`; no model weights are shipped.

## Use from main_computer

Install the update with `new_patch.py`, then execute:

```text
python -m tools.nanojev_qwen_hybridagent prepare --device cuda --allow-model-download
python -m tools.nanojev_qwen_hybridagent_training_smoke --device cuda
```

Future runs without `--allow-model-download` are offline. To use the instruction-tuned model in a *new* v2 output, explicitly supply `--model Qwen/Qwen3-0.6B --output C:\Users\subsi\NanoJev\runs\qwen3_06b_instruct_hybridagent_ln_v2` to both commands and `--allow-instruct-backbone` to the smoke. This never migrates the v1 state.

Optional fixed-depth generation:

```text
python -m tools.nanojev_qwen_hybridagent generate --device cuda --latent-steps 4 --prompt "A + (-A + 1) = "
```

Do not interpret untrained generation as arithmetic competence. Dynamic exit is deliberately disabled until there is a trained checkpoint. No external numerical executor, data router, or alternative arithmetic model is added.

## Testing

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_hybridagent.py tests/test_nanojev_qwen_hybridagent_training_smoke.py
```

CPU tests use a toy backbone. The live CUDA smoke must execute on the Windows machine with cached Qwen weights. The training, unseen-matrix, accuracy, adaptive switching and optimizer memory experiments have not been performed.

Official released architecture: https://raw.githubusercontent.com/togethercomputer/HybridAgent/main/hybrid_agent/hybridlink.py
