# Qwen HybridLink — task-specific causal joint-training experiment

This is a **new, uncertified** training lineage. It does not modify the original model, numerical LoRA checkpoint, format checkpoint, old repaired head, or their gate reports.

## Install and run

From the Main Computer root in the normal Qwen/CUDA Python environment:

```text
python -m tools.nanojev_qwen_hybrid_causal_joint --device cuda
```

Or stage/resume independently:

```text
python -m tools.nanojev_qwen_hybrid_causal_joint --stage train --device cuda --stop-after 60
python -m tools.nanojev_qwen_hybrid_causal_joint --stage train --device cuda --resume
python -m tools.nanojev_qwen_hybrid_causal_joint --stage gate --device cuda
```

Resuming **requires the same training arguments**, notably `--steps` and `--seed`; `--stop-after` does not enter the checkpoint configuration. The test gate runs on the untouched test set once and refuses repeat evaluation at the same output location. A `gate_started.json` sentinel consumes the holdout before the first inference case, even if the run later fails or is interrupted. Use a new output directory for an independent predeclared experiment; do not tune against test results.

Default output: `C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_causal_joint_v1` (actual default derives from home directory).

## What trains

- Restore the verified Qwen marker/format interface and its numerical attention LoRA; restore the geometrically repaired HybridLink with its learned output gain.
- Freeze all pretrained Qwen weights and both saved checkpoints. Train **only** a new copy of the HybridLink plus gain and the existing Qwen attention `q_proj`/`v_proj` LoRA tensors.
- First pass reads *only* `Question: Compute <expression>.\nReasoning: <latent>` and computes the source hidden state under `no_grad` to fit a 6 GB VRAM budget.
- HybridLink generates **one** latent vector; the second, differentiable Qwen pass consumes that same vector after the marker and before the exit marker.
- Optimize **all numerical-answer tokens** under ordinary teacher-forced autoregressive cross entropy, not a single answer-token score or a geometry proxy.
- Every fourth step, add a smaller autoregressive loss on a true task-dependent arithmetic explanation. Teacher trajectories include explicitly checked `a + (-a) = 0` and `u + v = delta`. This is supervised *target text only*. No teacher thoughts, expected answer labels, or oracle vectors enter latent generation or inference.
- Every second step, add a correct-versus-wrong/zero margin using a different prompt's **self-generated** latent as a control. Counterfactual logits are stop-gradient; they cannot be mistaken for backpropagation through a negative branch.
- Select the `best.pt` checkpoint using **development-set full answer cross entropy** only. Never use test accuracy for checkpoint selection.

This is a two-pass, stop-gradient-source approximation to jointly training the latent head and backbone attention LoRA. It is **not** full-parameter, full-gradient HybridSFT and is not claimed to reproduce the paper's results.

## Final gate

18 independent-seed magnitude-disjoint test problems, balanced across direct/composed, negative/zero/positive. Conditions use the **same new LoRA weights**, same gold-free question, and forced literal `\nAnswer: ` suffix:

1. Intact self-generated latent.
2. Zero latent.
3. A different test prompt's self-generated latent (no gold answer used to select it).
4. Entry and exit markers only.
5. Text-only without markers.

The scorer accepts only an integer **at the beginning** of the generated answer continuation; it does not scan later hallucinated answers. PASS requires intact to beat zero and shuffled by at least 2/18, be no more than one below text-only, and parse at least 17/18. If it fails, `deeper_training_permitted=false`; no recurrence-depth expansion is launched.

## Artifacts

- `training/config.json`: pinned sources, data split fingerprint, optimizer hyperparameters, algorithm declaration.
- `training/last.pt`: exact-stage resume point including optimizer and RNG state.
- `training/best.pt`: trainable head and LoRA tensors chosen on development data.
- `training/result.json`: progress and selected checkpoint, **uncertified**.
- `gate_result.json`: one-time test results, controls, separate `deeper_training_permitted` bit.

Relevant stdout: `HYBRID_CAUSAL_STEP`, `HYBRID_CAUSAL_TRAIN_RESULT`, `HYBRID_CAUSAL_CASE`, `HYBRID_CAUSAL_GATE_RESULT`. Errors fail closed as `HYBRID_CAUSAL_REFUSED`.

## Methodological limits

- The training data are the existing synthetic signed-cancellation family (new random seed and disjoint magnitudes). This tests one capability, not general agentic reasoning, arbitrary arithmetic, or the whole published HybridAgent method.
- The method does not assume one latent state can always encode an arbitrarily long derivation. Its one-step limit is intentional to isolate whether any causal benefit is learned before recurrent training.
- A passed test is only evidence on this 18-example benchmark and is **not** paper-reproduction certification. Larger new-holdout and beyond-distribution evaluations should follow.
- Initial training has not been executed on the user's GPU by the patch author. CUDA execution may require environment-specific debugging; available local tests use CPU mocks.
