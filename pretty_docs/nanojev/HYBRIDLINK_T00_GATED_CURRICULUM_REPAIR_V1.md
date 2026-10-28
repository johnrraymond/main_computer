# HybridLink T00-Gated Curriculum Repair (v1)

## Why this cutover exists

The Patch 3 smoke train ran 80 steps, cycling T00–T08 about every ten steps. Its `max_unroll=2` sliced the **first** two transitions at each deeper rung, so many updates never included a completed answer. It updated the Answer Detector alongside the generator and assigned its generated intermediate vectors mostly CONTINUE labels. It then selected `best.pt` even though all tested rollouts had **zero successful answer exits**. The diagnostic confirmed 12/12 frozen gold answers were recognized but 0/12 generated T00 states were recognized. It also measured a positive, but insufficient, improvement toward gold and a positive intact-versus-zero feedback gain.

**This repair addresses those three training-selection defects, without changing old scripts or checkpoints.** Install it alongside Patch 3, the frozen-detector patch, and T00 diagnostics.

## Fixed architecture and boundaries

1. Qwen (Stage 1) is **frozen**. The existing Stage 1 probe is **frozen**.
2. Both numerical readouts, including complex `(a,b)` and log radius, are **frozen diagnostic observers**. This is **not** a proof of geometrical causality or a new complex training loss.
3. The previously trained Answer Detector is **frozen**. Its gradients can pass *through its function* into the generated hidden state, but no detector weights are changed. No unbalanced negative detector retraining occurs.
4. **Only the HybridLink embedding generator is optimized.** For T00, every supervised training example is the final `PROBLEM → ANSWER` transition from a complete root chain, regardless of the chain's original depth. Gold target hidden vectors are training losses only.
5. All DEV decisions use **actual Qwen-generated feedback**. The reference target vector is used only *after* each generated state has been computed, for scoring. No gold intermediate state or numerical oracle controls the runtime.
6. The LM/vocabulary head is never called. An unchanged vector does not imply a correct answer; the detector still governs completion, with a separate stalled-state check.

## Staged curriculum

**T00:** Train the final hop for at least `--min-steps-per-rung` steps. Assess on disjoint DEV final-hop starting problems. Require a meaningful fraction of **generated** answer exits, the original detector's gold-answer recall, positive gain in answer-state alignment over the starting state, and positive alignment gain over a zero-latent embedding. Also require **absolute** generated-to-reference cosine >= `--min-gold-cosine` so detector-only adversarial activation cannot pass the gate. Defaults are moderate research gates, **not proof of mathematical correctness**.

**T01 and onward:** Opt in with `--max-rung 1` … `8`. The trainer cannot enter a new rung before the previous rung passes the DEV gate. On deeper rungs, at least half of training windows are anchored at the final answer; other windows cover earlier transitions. Unroll length remains bounded by `--max-unroll`. The stage gate evaluates **an autonomous rollout from that stage's starting problem**, with no oracle intermediate states. Every later gate repeats the T00 retention check.

**Candidate vs. promotion:** The experimental best candidate lives in `candidate.pt` and is explicitly labeled `PROVISIONAL_NOT_PROMOTED`. `best.pt` and `accepted_T00.pt` are written **only after the gate passes**. A zero-success run can never produce a new promoted checkpoint. The full run always writes `progress.json`, `history.json`, and `result.json`, including gate failures and detected regressions. Do not delete `hybridlink_latent_loop_smoke_v1/best.pt` or your previous diagnostic reports.

The default `--max-rung 0` intentionally stops after accepting T00. Move outward only after reviewing these measurements.

## Install

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\hybridlink_t00_gated_curriculum_repair_v1.zip'
```

## Optional: establish whether one latent embedding can reach a gold answer

This per-example **optimization ceiling** uses a separate trainable input embedding for each selected DEV case, while all model weights remain frozen. It can distinguish a difficult generator-learning problem from an embedding/Qwen feedback interface that cannot reach answer-like states even with direct per-example optimization.

```powershell
& $py -m tools.nanojev_hybridlink_t00_curriculum_repair `
  --stage reachability --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --warm-start-loop "C:\Users\subsi\NanoJev\runs\hybridlink_latent_loop_smoke_v1\best.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --reachability-cases 4 --reachability-steps 60 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_t00_reachability_v1"
```

`T00_REPAIR_REACHABILITY` contains mean starting cosine, optimized cosine, and how many optimized embeddings pass the frozen detector. Individual examples are in `reachability.json`. This is a **representational ceiling**, not generalization; the optimized embeddings are not saved as a learned model or supplied during runtime.

## T00-only training (first run)

```powershell
& $py -m tools.nanojev_hybridlink_t00_curriculum_repair `
  --stage train --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --warm-start-loop "C:\Users\subsi\NanoJev\runs\hybridlink_latent_loop_smoke_v1\best.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --steps 600 --eval-every 50 --eval-cases 12 `
  --min-steps-per-rung 100 --max-rung 0 `
  --max-unroll 2 --max-depth 14 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_t00_repair_v1"
```

At each `T00_REPAIR_PROGRESS`, inspect:

- `dev.correct_stop_depth` / `dev.cases`, `dev.gold_answer_exits`, `dev.mean_generated_to_gold_cosine`
- `dev.mean_gain_over_source_cosine`, `dev.mean_intact_minus_zero_cosine`
- `t00_retention.mean_direct_code_abs_error`, `mean_complex_code_abs_error`, `unity_cases`, `mean_unity_radius_error` (diagnostic only)
- `gate_pass` and `gate_reasons`; `detector_updated:false`, `qwen_updated:false`, `geometry_feedback_causal:false`

**Promotion gates (default):** 12 cases evaluated, at least 6; at least 50% generated correct stops; no early false stops; >=80% gold detector positive; >=0.70 generated-to-gold cosine; >=0.02 gain over starting state; >=0.01 gain over zero-latent, and >=100 training steps at that rung. These gates can be tuned from CLI, but a lower bar is not evidence of a learned useful latent ladder.

If unsuccessful, `result.status=T00_NOT_PASSED` and `checkpoint=null`; use `candidate.pt` only for analysis and DO NOT treat it as promoted. If successful, `best.pt` and `accepted_T00.pt` appear, but status remains `T00_GATE_PASSED_NOT_FULL_LATENT_CERTIFIED` until a longer independent rollout has passed.

## Probe any saved checkpoint

New checkpoints retain the original `nanojev.hybridlink.generated-latent-loop.v1` inference schema and are accepted by the existing Patch 3 `probe` and `inspect` stages. Use `candidate.pt` if no gate passed; otherwise use `best.pt`.

```powershell
& $py -m tools.nanojev_hybridlink_latent_loop_cutover `
  --stage probe --split dev --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_t00_repair_v1\best.pt" `
  --eval-cases 16
```

## Expand only when T00 succeeds

Once T00 is accepted, launch **a new run/output directory**, warm-starting `accepted_T00.pt` and setting `--max-rung 1`. For subsequent stages, repeat with `--max-rung 2`, etc. It is also possible to set `--max-rung 8` from the start; the gate prevents accidental advancement, but an unaccepted rung will consume the remainder of the budget without automatically advancing.

## Tests and limitations

```powershell
& $py -m pytest tests/test_nanojev_hybridlink_t00_curriculum_repair.py -q
```

CPU tests cover deep-root T00 selection, correct answer-anchored truncated windows, immutable Qwen/detector, separate unpromoted candidate vs accepted checkpoint, prototype compatibility with Patch 3 `inspect`, isolated reachability optimization and fail-closed warm start provenance. GPU results and successful T00 generalization cannot be claimed until the above CUDA run finishes. This patch intentionally **does not** train the complex coordinates into HybridLink transitions; the geometry remains observer-only pending a separate causal loss/input ablation.
