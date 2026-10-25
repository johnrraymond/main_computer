# HybridLink activation: blind recurrent transfer probe (Qwen3-0.6B-Base)

## Purpose and decision

This is a **read-only comparison** of the original initialized HybridLink against the best head from the already completed 80-step activation run. It runs the same Qwen3-0.6B-Base backbone, prompt, text/latent markers, **four recurrent latent steps**, fixed answer cue, and greedy answer decoding for both arms. It does not give either arm teacher thoughts, target embeddings, or expected answers during inference. Gold labels enter only the scoring function **after each arm has generated its continuation**.

It reports exact-answer accuracy on 16 precommitted cancellation challenges, broken down by negative, zero, and positive results. Eight are direct `A+(-A+u)=u` examples; eight compose another residual `A+(-A+u)+v=u+v`, preventing every target from simply being a literal input value. Magnitudes exceed 1,000, include positive/negative A, and are absent from the prior activation train/dev lists. The experiment is *held out by magnitude and exact example*, **not** a certificate of cross-template generalization or absence of all prior pretraining exposure.

It also reports paired wins/regressions, mean probability of the **first correct-answer token** after the same answer cue, and the difference in the two heads' downstream recurrent hidden states. Token-probability is diagnostic; it is **not** full-answer likelihood and does not determine the acceptance gate. Parsing recognizes only a signed standalone integer on the first output line; unparseable output is scored incorrect.

**Precommitted exploratory gate:** `NUMERICAL_TRANSFER_OBSERVED` requires at least **two net additional correct cases** out of 16 for the trained head. This is an exploratory small-sample threshold, not statistical significance or reliable numerical competence. Otherwise the command exits 1 with `NO_MEASURABLE_ACCURACY_GAIN` or `NO_NUMERICAL_TRANSFER_DEMONSTRATED`. Exit 2 is a genuine harness/precondition failure (`HYBRID_TRANSFER_REFUSED`), distinct from a valid negative experimental result. A positive exploratory result is also **not** a release gate.

## Install and run

From `main_computer`, with the same Python environment that passed the activation run:

```text
python new_patch.py nanojev_qwen_hybridlink_transfer_probe_patch.zip --dry-run
python new_patch.py nanojev_qwen_hybridlink_transfer_probe_patch.zip
python -m tools.nanojev_qwen_hybridagent_transfer --device cuda
```

Defaults:

- Original, untrained **v2 Base** checkpoint: `~/NanoJev/runs/qwen3_06b_base_hybridagent_ln_v2`
- Completed activation run: `~/NanoJev/runs/qwen3_06b_base_hybridlink_activation_probe_v1`
- **New output**: `~/NanoJev/runs/qwen3_06b_base_hybridlink_transfer_v1/result.json`
- Head: `best_head.pt`; use `--use-last` for a separately named follow-up run if comparing the final `last.pt` state.
- `--latent-steps 4 --max-new-tokens 12 --min-net-gain 2`

There is **no optimizer**, no training, no checkpoint mutation, and no default download. A new output directory must be used for every attempt. The evaluator checks the recorded activation run signature, baseline path and hashes, model revision, run completion, best-head stage/step/dev loss, strict head tensor names and shapes, absence of backbone changes, and unchanged source files. It refuses a missing, untrained, mismatched, or non-finite trained head rather than inventing a fallback.

Each completed challenge emits `HYBRID_TRANSFER_CASE={...}`; the final record is `HYBRID_TRANSFER_RESULT={...}` and includes the paired accuracy, per-sign breakdown and complete case records. The result is also saved as `result.json`. A genuine negative outcome is valuable evidence that head-only teacher embedding alignment did not visibly improve downstream autonomous computation; the response should then be **format activation / self-conditioned training**, not training the same activation objective indefinitely.

### CPU contract tests

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_hybridagent_transfer.py
```

These tests use a tiny fake backbone for contracts only, never pretending to establish Qwen numerical accuracy. The actual `run` command must execute on the user's CUDA machine, using the real Qwen weights and the actual `best_head.pt` from the completed activation run.
