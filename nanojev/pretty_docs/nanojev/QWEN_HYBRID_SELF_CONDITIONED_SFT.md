# Qwen3-0.6B HybridAgent: bounded self-conditioned HybridSFT

This harness implements a **two-pass, joint LoRA + HybridLink** training experiment following the self-conditioned phase of Zou et al., *Hybrid Reasoning Agents* (2026), Section 4.1 and the released `hybrid_sft/model.py`: <https://arxiv.org/html/2610.18861> and <https://github.com/togethercomputer/HybridAgent>.

It is not a full published 8-GPU SFT reproduction. Differences are explicitly recorded: it runs on a single 6-GB GPU, the full pretrained Qwen backbone remains frozen while its attention LoRA trains, the *first decoder pass is detached* to bound memory, the separate format-activation phase is not complete in this track, and the miniature dataset is a numerical diagnostic rather than the authors' 54K hybrid agent trajectories. It does not implement HybridRL, learned exit decisions, tools, or inspectable latent readout.

## Prerequisites

Run the preceding preparation, head activation, and numerical-backbone SFT phases **successfully**. This harness insists on their completed run metadata, common pinned Qwen3-0.6B-Base model, intact baseline hashes, and the actual saved `best_head.pt` and `best_lora.pt`. No synthetic fallback model is used for the real CUDA run.

Default source directories:

- `~/NanoJev/runs/qwen3_06b_base_hybridagent_ln_v2`
- `~/NanoJev/runs/qwen3_06b_base_hybridlink_activation_probe_v1`
- `~/NanoJev/runs/qwen3_06b_zero_line_backbone_sft_v1`

The combined output is **new**: `~/NanoJev/runs/qwen3_06b_hybrid_self_conditioned_v1`. The script refuses to write into, overwrite, or mutate a source directory or checkpoint. It does not download model weights without explicit permission.

## Install and run

From `main_computer`, using the already working `.venv-diagram` Python interpreter:

```text
python new_patch.py nanojev_qwen_hybrid_self_conditioned_sft_patch.zip --dry-run
python new_patch.py nanojev_qwen_hybrid_self_conditioned_sft_patch.zip
python -m tools.nanojev_qwen_hybridagent_self_conditioned --device cuda
```

The default run performs **12 training steps**, BF16 on supported CUDA devices, 5e-5 LoRA and 5e-5 HybridLink learning rates, and development checks every 4 steps. The six held-out problems are evaluated **before and after** using recurrent generated latent embeddings; no teacher thoughts or correct answers are supplied to the inference path.

For a checkpointed partial run, use a clean output and:

```text
python -m tools.nanojev_qwen_hybridagent_self_conditioned --device cuda --stop-after 4
python -m tools.nanojev_qwen_hybridagent_self_conditioned --device cuda --resume
```

Resume rejects changes to steps, data, hyperparameters, input checkpoint hashes, Base model identity, and source paths. It restores head/LoRA weights, AdamW state, and PyTorch CPU/CUDA RNG states. `best.pt` captures the best observed held-out **training objective**, not numerical-certification accuracy. `last.pt` is a resumable state and `result.json` is a report.

## What the two passes do

1. Create a teacher-forced sequence containing the numerical expression, atomic `<latent>` entry, a latent thought span, atomic `</latent>` exit, and a supervised text answer. The question is never a supervised target. The entry and exit are text targets; **entry and thought tokens are latent source positions**, exit receives twice the ordinary CE weight.
2. Decoder pass 1 reads ground-truth embeddings to produce hidden states. To fit one small GPU, decoder hidden states are detached **before** HybridLink produces the next latent input vectors. These are not answer labels: they are the model's own continuous vector predictions.
3. Decoder pass 2 reads exactly the same sequence with predicted HybridLink embeddings substituted at positions immediately after latent source tokens, including the exit anchor input. **Neither predicted embeddings nor their gradients are detached.**
4. Optimize two separate parameter groups simultaneously: Qwen attention LoRA (text cross-entropy + feedback gradients) and HybridLink (text feedback gradients + cosine/MSE regression, with a dedicated exit regression). Original pretrained Qwen weights and the source checkpoints are frozen and byte-checked.

The program asserts that teacher-forced and self-conditioned text losses differ, that replacement embeddings differ from teacher inputs, that CE propagates gradients into HybridLink, that gradients to both LoRA and HybridLink are present/finite/nonzero, and that output heads and text targets are correctly masked. It refuses to report success on mechanical disconnection.

## Report interpretation

- `HYBRID_SELF_CONDITIONED_STEP` shows step, text CE, link loss, feedback delta, development loss, and best development loss.
- `HYBRID_SELF_CONDITIONED_RESULT` ends with `SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED` or `PAUSED_RESUMABLE`.
- `initial_blind`, `blind`, and `blind_net_correct_gain` compare before/after free-running recurrent inference on identical held-out questions.
- `oracle_latent_states_supplied_at_inference` must be 0; `source_mutations` must be 0.
- `cuda_peak_allocated_mb` measures VRAM. No architecture or memory claim is made before live CUDA execution.

**Completion certifies training mechanics only.** The loss may decrease without a correct numerical computation. Adaptive latent entry/exit, efficient agentic tool use, causal reliance on semantic latent reasoning, and multi-task performance remain untested. This phase is the missing integrated training mechanism, not a substitute for later literature-based certification.

## Offline tests

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_hybridagent_self_conditioned.py
```

The tests simulate a tiny causal backbone but verify actual two-pass tensor and gradient behavior, checkpoint save/resume equality, strict source provenance, read-only inference prompts, latent masking, and optimizer scope. Live Qwen CUDA training is intentionally not claimed as already tested.

## Mixed-precision contract (BF16 Qwen / FP32 HybridLink)

The verified Qwen checkpoint may be loaded in BF16 while HybridLink and the LoRA
updates remain FP32. This requires explicit, **differentiable** casts at module
boundaries: Qwen hidden states enter HybridLink in its parameter dtype; predicted
latent embeddings enter Qwen in its input-embedding dtype; and selected decoder
hidden states enter the output projection in its weight dtype. The CE loss is
then computed in FP32. These casts do not detach the graph or relax gradient
assertions. They avoid the earlier `expected scalar type BFloat16 but found Float`
error both during pre-training evaluation and recurrent inference.

This patch does not change the training objective, hyperparameters, source
checkpoints, frozen-weight rules, or optimizer groups. A failed first evaluation
occurs before this script creates its output directory, so the corrected run may
normally be restarted using the original command. If another dtype error
occurs, its precise operation will require the traceback; no CUDA execution of
this correction is claimed by the CPU tests.
