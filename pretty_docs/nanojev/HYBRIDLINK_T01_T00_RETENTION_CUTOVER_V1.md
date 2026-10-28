# HybridLink T01 two-hop cutover with mandatory T00 retention (v1)

## Purpose

Extend the **accepted T00** generator at `hybridlink_t00_repair_v1/best.pt` to the next actual two-step reduction, T01. Previously T00 succeeded on 12/12 selected DEV cases at step 100. **This patch does not claim T01 is trained or successful until actual GPU-generated DEV rollouts pass.** It adds a separate trainer and does not edit the T00 or Patch 3 scripts/checkpoints.

**T01 is specifically:** `PROBLEM (two remaining operations)` -> `PROBLEM (one remaining operation)` -> `ANSWER (root completed)`. Qwen receives one generated HybridLink embedding at each hop, not intermediate text or gold hidden vectors. An independent frozen Answer Detector must say CONTINUE at hop one and ANSWER at hop two. Guardrail depth 2 is **an evaluation horizon, not a substitute for the detector**. The Qwen vocabulary head is unused.

## Invariants and training

- Warm start requires a **promoted T00 checkpoint**, including the exact source Qwen, detector, and LoRA lineage. The trainer verifies the checkpoint was accepted at rung zero, not a `candidate.pt` or old 80-step `best.pt`.
- Qwen backbone, Stage 1 probe, numerical and complex readouts, and the Answer Detector remain frozen. Only HybridLink's continuous-embedding generator parameters are trained. Gradients flow through Qwen and detector into the generator, without updating their parameters.
- Every non-replay training example consists of **both** supervised transitions, including its final `ANSWER`, with a two-step differentiable Qwen unroll. The target hidden states and detector labels are used for the training loss, not inference.
- 30% of training examples replay acquired T00 final hops by default; this replay is not used as the acceptance test.
- DEV T01 rollouts receive **only their initial `PROBLEM` string**; the expected intermediate and final states are consulted strictly *after* generation to calculate cosine distances and numerical readout errors. Zero-latent ablation uses the same starting problem.
- Each DEV evaluation independently reruns the **same T00 cases** scored before training. Any previously successful T00 exit that ceases to work blocks T01 promotion, even if the average exit rate looks good.
- A drop in mean T00 target cosine greater than 0.025 also blocks promotion. An adjustable T00 exit-rate floor (default 100%) applies in addition to the per-case no-regression rule.
- The complex `(a,b)` readout is still **observer-only**; this patch does not pretend it causally improves feedback. The per-step direct and complex losses are logged as numerical diagnostics, not acceptance gates.
- A failed candidate is saved as `candidate.pt`, with **no** promoted `best.pt` or `accepted_T01.pt`. Original T00 `best.pt` is never overwritten.

## Install

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\hybridlink_t01_t00_retention_cutover_v1.zip'
```

## Preview without model load

```powershell
& $py -m tools.nanojev_hybridlink_t01_retention_cutover `
  --stage preview --device cpu `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

## Train only T01 with T00 replay and retention gate

```powershell
& $py -m tools.nanojev_hybridlink_t01_retention_cutover `
  --stage train --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --warm-start-t00 "C:\Users\subsi\NanoJev\runs\hybridlink_t00_repair_v1\best.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --steps 600 --eval-every 50 --eval-cases 12 --t00-cases 12 `
  --min-t01-steps 100 --max-unroll 2 `
  --t00-replay-fraction 0.30 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_t01_retention_v1"
```

The first run may need to encode eight- or dozens-of-target DEV reference vectors into the immutable shared cache. Frozen train/dev source vectors should produce `FROZEN_CACHE_HIT`. Subsequent runs with matching source identity reuse the T01 target cache too.

`T01_RETENTION_BASELINE`: before any T01 training, this verifies the warm-start T00 generator still passes the selected source cases, and records their exact baseline vectors and exit decisions. If the required exit-rate floor fails, training refuses to proceed.

`T01_RETENTION_PROGRESS`: emitted each evaluation and at the final step. The following fields are the key audit trail:

| Field | Meaning |
|---|---|
| `t01.first_step_continued` | Detector did not terminate before the intermediate reduction |
| `t01.second_step_answer` | Detector recognized an answer at the second generated hop |
| `t01.correct_stop_depth` | Counts cases satisfying BOTH conditions |
| `t01.early_false_stops` | Early erroneous ANSWER at step one; acceptance requires **zero** |
| `t01.mean_first_step_gold_cosine` | Generated intermediate versus frozen Qwen intermediate reference |
| `t01.mean_final_gold_cosine` | Final generated state versus frozen Qwen answer reference |
| `t01.mean_intact_minus_zero_cosine` | Final gold similarity advantage over zero-latent feedback |
| `t01.mean_first_step_p_answer` | Detector confidence while computation should still be unfinished |
| `t01.mean_second_step_p_answer` | Detector confidence at the completed root |
| `t01.mean_first/second_step_direct_code_error` | Observational direct numerical error at each hop |
| `t01.mean_first/second_step_complex_code_error` | Observational complex numerical error at each hop |
| `retention.lost_sources` | Previously passing T00 sources that failed at this update; **must be empty** |
| `retention.mean_gold_cosine_drop` | T00 representation retention against its pre-train baseline |
| `gate_reasons` | Exact blocking conditions when T01 fails |

Promotion defaults require at least **100 T01 training updates** (including T00 replay), at least eight T01 DEV cases, at least **75% correct two-hop stops**, **zero** early false stops, first-hop mean reference cosine >=0.50, second-hop mean reference cosine >=0.70, positive intact-over-zero gain >=0.01, 100% T00 reference-case exits retained and <=0.025 drop in T00 mean cosine. These are research gates, not a claim of arithmetic correctness.

Output files: `t00_baseline.json`, `latest_t01_cases.json` (per-example/per-step traces), `progress.json`, `history.json`, `result.json`, `candidate.pt`, and, **only when accepted**, `accepted_T01.pt` plus `best.pt`.

Accepted checkpoints retain the original Patch 3 latent loop schema, so Patch 3's existing `--stage inspect` and `--stage probe` can load them. Output status remains `T01_ACCEPTED_NOT_FULL_LATENT_CERTIFIED`, since T02–T08 and independent numerical correctness remain unproven.

## Read-only probe on a saved T01 candidate or accepted checkpoint

```powershell
& $py -m tools.nanojev_hybridlink_t01_retention_cutover `
  --stage probe --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --warm-start-t00 "C:\Users\subsi\NanoJev\runs\hybridlink_t00_repair_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_t01_retention_v1\candidate.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --eval-cases 12 --t00-cases 12 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_t01_probe_v1"
```

Use `best.pt` instead of `candidate.pt` after a gate passes. The `probe.json` includes detailed two-hop case traces and the current T00 retention metrics.

## Tests

```powershell
& $py -m pytest tests/test_nanojev_hybridlink_t01_retention_cutover.py -q
```

The CPU harness uses a mock Qwen and frozen detector for full two-hop feedback, deliberately stubs DEV scores only to test accepted-checkpoint serialization and runtime compatibility, and independently checks gate/retention false positives. No CUDA accuracy is implied by CPU test success.
