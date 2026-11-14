# Qwen3-0.6B-Base — first controlled HybridLink-activation experiment

## Purpose

Run a bounded **real optimizer experiment** on the installed v2 Qwen3-0.6B-Base + normalized HybridLink. This is an isolated check of the *second* HybridSFT stage, **HybridLink activation**. It deliberately does **not** claim to have completed stage 1 (format activation), stage 3 (self-conditioned training), or HybridRL.

The experiment implements the authors' released activation regression objective at latent positions:

```text
HybridLink(hidden[t]) -> Emb(token[t+1])
loss = mean(1 - cosine_similarity) + 0.2 * mean(per-dimension MSE)
     + mean_on_exit(1 - cosine_similarity) + 0.2 * mean_on_exit(per-dimension MSE)
```

The exit term targets the embedding of `</latent>` from the final latent payload state. The training trajectory includes the `<latent>` entry marker. Both boundary token positions are explicitly checked. Text cross entropy is omitted from the *optimizer loss* because the backbone is completely frozen in this isolated stage; the published recipe's full stage-2 objective includes backbone CE, but that term has zero gradient with respect to HybridLink here. This component smoke is not the complete released multi-GPU trainer.

Backbone hidden states and target embeddings are computed from **actual Qwen3-0.6B-Base with a teacher-forced input sequence** under `torch.no_grad()`. They are cached in small CPU tensors. Only the v2 HybridLink parameters are optimized (FP32 head, AdamW; LR 2e-4, betas 0.9/0.95, weight decay 0, max grad norm 1). This is mathematically equivalent to repeated backbone passes for this head-only fixed-teacher objective, while avoiding the activation memory and runtime cost of recomputing Qwen for every optimizer step. There is **no oracle evaluation and no claimed numerical reasoning improvement**.

## Run from `main_computer`

Prerequisite: successful `nanojev_qwen_hybridagent_training_smoke` with `status=PASS_WITH_LIMITATIONS`, using your prepared v2 Base checkpoint.

```text
python -m tools.nanojev_qwen_hybridagent_activation --device cuda
```

By default it performs 80 optimizer steps with 32 teacher latent-source/next-embedding pairs per step, evaluating every 10 steps against a disjoint four-example diagnostic split. No weight downloads happen unless `--allow-model-download` is explicitly supplied.

For a first 12-step exercise with a separate output lineage:

```text
python -m tools.nanojev_qwen_hybridagent_activation --device cuda --steps 12 --output C:\Users\subsi\NanoJev\runs\hybridlink_activation_12step_probe
```

For a resumability experiment, stop after step 20 and later continue the **same planned 80-step run**:

```text
python -m tools.nanojev_qwen_hybridagent_activation --device cuda --steps 80 --stop-after 20 --output C:\Users\subsi\NanoJev\runs\hybridlink_activation_resume_probe
python -m tools.nanojev_qwen_hybridagent_activation --device cuda --steps 80 --resume --output C:\Users\subsi\NanoJev\runs\hybridlink_activation_resume_probe
```

The original v2 checkpoint at `~/NanoJev/runs/qwen3_06b_base_hybridagent_ln_v2` is **read-only**. Outputs are always in a separate directory, by default `~/NanoJev/runs/qwen3_06b_base_hybridlink_activation_probe_v1`. The trainer refuses an existing output folder unless `--resume`. It also refuses any output path equal to the baseline path.

## Outputs

- `run_config.json`: base checkpoint paths and hashes, model revision, dataset hash, optimizer settings, and the experiment's exact objective. A resumed invocation must match these fields.
- `last.pt`: HybridLink weights, full AdamW optimizer state, step count, and initial-loss anchors. Atomic replacement, recoverable via `--resume`.
- `best_head.pt`: head weights with the best diagnostic loss and a **non-certified adaptive switching** flag. This file is **not** an autonomously deployable HybridAgent checkpoint.
- `result.json`: initial/current/final train and diagnostic losses, baseline hashes, optimizer-step counts, training-data counts, and peak CUDA allocated/reserved memory.

Expected final `HYBRID_ACTIVATION_RESULT` has `"status":"PASS_MECHANICS_ONLY"` only when (a) gradients are present and finite, (b) head weights changed, (c) the frozen backbone and original saved adapter remain bitwise unchanged, (d) exact optimizer progress and checkpointing worked, and (e) the full teacher-forced training loss decreased by more than 0.0001. `"status":"PAUSED_RESUMABLE"` means an intentional `--stop-after` interruption; it is **not** a full experiment pass.

The validation metric is *teacher-forced latent embedding alignment*, not X/Y numerical accuracy. The tiny examples are built into the tool and split by exact `(a,b)` pairs. The diagnostic set is distinct, but all examples use the same generated cancellation template. It does **not** establish unseen-matrix generalization or oracle-blindness.

## Regression tests

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_hybridagent_activation.py
```

These tests use a CPU toy backbone only to exercise the trainer contract; the training command uses **real Qwen weights**. The regression suite checks latent/exit masks, the published objective, finite teacher pairs, optimizer membership, exact pause/resume equality, prevention of baseline overwrites, and hash-sensitive resume refusal.

## Next stage, explicitly not included

A proper future HybridSFT trajectory needs prior format activation followed by activation on those weights, then two-pass self-conditioned training. A successful result here demonstrates only that the isolated HybridLink optimizer, teacher targets, loss, and single-GPU memory budget are operational. Do not use it to certify inference, dynamic latent exit, numerical generalization, or release readiness.

Sources: https://arxiv.org/html/2610.18861 ; https://github.com/togethercomputer/HybridAgent/tree/main/hybrid_sft
