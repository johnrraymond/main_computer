# HybridAgent one-step gate: trained output-marker restoration on tied Qwen embeddings

## Root cause

Qwen3-0.6B-Base sets `tie_word_embeddings=true`: its `lm_head.weight` and input-embedding weights share storage. The interface-repair stage deliberately trains two *independent* sets of special-token rows, `input_rows` and `output_rows`. During saved checkpoint reload, the previous `MarkerRows.apply_to_model` installed the input rows but skipped the output rows when storage was tied. Then `gate()` constructed fresh output rows from the **input** embedding matrix. That discarded the learned output marker rows from the real inference scorer. The result was almost-perfect teacher-forced checkpoint-time marker probabilities versus nearly zero gate-time probabilities.

## Fix

The restore step leaves the tied embedding matrix untouched except for the two learned input rows. When tied, it attaches a narrowly scoped `lm_head` forward hook that substitutes the **two learned output-marker logits** from the saved FP32 `output_rows` on every real Qwen causal forward, including cached latent steps. All other vocabulary logits are untouched; it does not duplicate the 151k-token matrix, untie pretrained weights, or modify saved checkpoints. Untied output weights continue to be restored directly.

`gate` retains and uses the loaded checkpoint's saved marker rows rather than regenerating them from tied input weights. It asserts that checkpoint-time and reloaded marker probabilities and loss match (within a small tolerance), and that the *real inference* logits match the saved FP32 marker projection. A mismatch now refuses the gate instead of producing a misleading score. The first old gate result is copied to `gate_pre_tied_output.json` before a re-evaluation writes a new `gate_result.json`.

## Rerun just the gate

From `main_computer`, after installing the patch:

```text
python -m tools.nanojev_qwen_hybrid_interface_repair --stage gate --device cuda
```

**Do not rerun the completed 240-step format training or 120-step head alignment.** The original output under `~/NanoJev/runs/qwen3_06b_hybrid_interface_repair_v1` already contains the required completed checkpoints; this invocation only loads them and re-evaluates the six held-out cases.

A healthy checkpoint restoration should produce `marker_checkpoint_roundtrip: true`, `tied_output_marker_override: true`, and `markers_pass: true`. This does **not** guarantee `latent_pass`. The free-running one-step latent path must still retain enough answer accuracy, beat the zeroed-vector control, and produce parseable text before deeper training is permitted. The gate remains fail-closed.

Offline CPU checks (including a tied-Qwen stand-in and a full read-only gate test):

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_hybrid_interface_repair.py
```

If a future trainer reuses `_load_format`, it receives the correct output-logit hook in the loaded model; existing `.pt` checkpoint formats do not change. This is a code-path correction, not proof of acquired latent reasoning.
