# HybridLink Generated Latent Loop — Patch 3 / v1

**Scope:** This patch connects the trained Stage 1 Qwen backbone and the Patch 1+2 frozen detector to an actual generated-embedding feedback loop. It creates `tools/nanojev_hybridlink_latent_loop_cutover.py`. It does not rewrite any existing scripts, parent checkpoints, numerical readout weights, or the text LM head.

## What is actually running

1. Encode the initial `PROBLEM: ...` once with the immutable Stage 1 Qwen decoder.
2. HybridLink's **newly trained** MLP converts the last hidden vector into a continuous *input embedding*.
3. Append that embedding to the original input-embedding sequence, and run **Qwen's decoder**, not its vocabulary head.
4. Read the last hidden vector; the separately trained **generated-state Answer Detector** decides whether to exit. If positive, read both pretrained numerical heads. Otherwise repeat using the generated vector.
5. Stop a persistent fixed-point **as STALLED, never as an answer**. The hard maximum depth and context budget are safety failures, not successful completion.

Training uses the supervised transitions in the Stage 1 deck for targets only. These target vectors are cached from the frozen Qwen representation. The training loop minimizes discrepancy between *post-feedback Qwen hidden states* and their labeled targets, and fine-tunes a **copy** of the Answer Detector to recognize generated terminal vs nonterminal states. The original detector and original Qwen remain unchanged. The training curriculum visits suffix starts from T00 outward through T08; `--max-unroll` controls BPTT depth but not which rung is selected. No ground-truth intermediate text is inserted during rollout.

**Limitations:** No latent-trained checkpoint has been certified on the real CUDA Qwen model yet. Alignment against label-prefixed target embeddings is training supervision, not evidence of semantic success. A generated-state detector may learn a step-count shortcut, and agreement between the numeric heads is insufficient to prove correct arithmetic. The DEV comparison is therefore required. Full-context re-evaluation at each step is currently slower than a cached-key/value decoder; the code prioritizes an auditable implementation.

## Install

Use the repository's normal `new_patch.py` installer:

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\hybridlink_generated_latent_loop_patch3_v1.zip'
```

## Preview (no GPU model load)

```powershell
& $py -m tools.nanojev_hybridlink_latent_loop_cutover `
  --stage preview --device cpu `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

## Train the real generated loop

Existing `hybridlink_frozen_shared_cache_v1` is reused by exact model/deck provenance (no re-encoding of unchanged teacher/parent vectors). A short smoke before longer training is strongly recommended: use `--steps 80 --eval-every 40 --eval-cases 6` and a different `--output` directory.

```powershell
& $py -m tools.nanojev_hybridlink_latent_loop_cutover `
  --stage train --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --steps 800 --eval-every 100 --eval-cases 16 `
  --max-unroll 2 --max-depth 14 `
  --max-tokens 192 --encoder-batch 12 `
  --lr 0.0001 --detector-lr 0.00005 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_generated_latent_loop_v1"
```

Training prints `LATENT_LOOP_PROGRESS` and saves a separate versioned `best.pt` and `best_dev_cases.json`. If GPU memory is constrained, reduce `--max-unroll 2` to `--max-unroll 1` and use a separate output directory. `max_depth` does not prescribe stopping; the detector does.

## Probe (intact versus zero-latent ablation)

```powershell
& $py -m tools.nanojev_hybridlink_latent_loop_cutover `
  --stage probe --split dev --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_generated_latent_loop_v1\best.pt" `
  --eval-cases 32
```

The probe runs **actual Qwen recurrence** with learned embeddings and a matched zero-embedding ablation. The gold path is consulted *only after the rollout* to measure premature stops, on-time stops, stalls and exhaustion, grouped over multiple rung depths. A positive intact-minus-zero score indicates a necessary but **not sufficient** contribution of the learned transition. A zero or negative differential fails the case for useful latent feedback. Don't infer numerical accuracy from correct exit depth; those are distinct measurements.

## Single headless rollout

```powershell
& $py -m tools.nanojev_hybridlink_latent_loop_cutover `
  --stage inspect --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_generated_latent_loop_v1\best.pt" `
  --state "PROBLEM: (2+(-1))+5"
```

Inspect returns `ANSWER`, `STALLED`, `BUDGET_EXHAUSTED`, or `CONTEXT_EXHAUSTED`; every step includes Answer Detector probability and matched-context relative vector drift. For an `ANSWER`, the direct and complex numerical values are reported, **not** claimed accurate by virtue of termination.

## Gates before promotion

- Frozen Qwen Stage 1 checkpoint and original frozen detector remain unchanged, verified by SHA256.
- No LM/vocabulary-head calls, no oracle-generated intermediate states at inference.
- Completed dev trajectories exit at their measured true depth, with low premature stops. Check depth coverage includes multiple-rung T00–T08.
- Intact transitions outperform the zero-latent ablation; otherwise recurrence is not yet demonstrated to be useful.
- Numerical readouts and complex geometry are reported independently; the existing numerical heads are not retrained or allowed to rewrite the backbone.
- A stable but unfinished generated vector is always `STALLED`, never silently promoted to a root answer.

Only after these gates should this version be wired into a production HybridLink executor. Until then its checkpoint status is `GENERATED_LATENT_UNCERTIFIED`.
