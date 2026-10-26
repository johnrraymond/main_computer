# Qwen3-0.6B HybridAgent: calibrated alignment repair and one-step gate

This experiment is **not** a claim that the HybridAgent paper's results have been reproduced. It is a fail-closed repair experiment after the read-only geometry diagnostic found that the trained HybridLink's raw output norm was around 31.5 while native Qwen embedding norms were around 0.9, and that norm correction alone did not outperform a zero-vector control.

## Install and launch

Apply the accompanying `main_computer_test/` ZIP through the repo's `new_patch.py` harness. Run from the repo root in the NanoJev environment:

```text
python -m tools.nanojev_qwen_hybrid_alignment_repair --device cuda
```

The default command performs all four stages and stops at the first failed gate. It does **not** rerun the 240-step marker-format training or overwrite the old 120-step aligned head. It uses the existing corrected gate and checkpoint fingerprints.

### Individual stages

```text
python -m tools.nanojev_qwen_hybrid_alignment_repair --stage calibrate --device cuda
python -m tools.nanojev_qwen_hybrid_alignment_repair --stage train --device cuda
python -m tools.nanojev_qwen_hybrid_alignment_repair --stage recurrence --device cuda
python -m tools.nanojev_qwen_hybrid_alignment_repair --stage gate --device cuda
```

The `calibrate` stage writes `calibration.json` using ONLY format-run development examples and never examines blind-test accuracy. It compares dev-only median-norm calibration with signed least-squares calibration. It refuses nonfinite or unsafe gains.

The `train` stage initializes an **external scalar output gain** and trains a new copy of the existing HybridLink (unmodified `pre_ln`/`proj1`/GELU/`proj2`/`post_ln` module) against development-separated teacher-generated embedding pairs. Training uses scale-aware relative MSE, target cosine, within-batch contrastive negatives with duplicate targets excluded, and norm loss. Teacher trajectories are never passed to free-running inference. The frozen Qwen backbone, numerical LoRA, original format checkpoint, and original aligned head are not mutated. Best-checkpoint selection uses development **geometry only** and does not look at blind-answer correctness.

The `recurrence` stage compares **identical generated latent vectors** through (1) incremental KV-cache execution and (2) a full-sequence forward pass, and independently compares cached versus full marker-only execution. It requires relative hidden and logit RMS errors at most 5% and equal next-token argmax predictions. Failed recurrence blocks the blind gate.

The `gate` stage compares text-only, marker-only, zero latent, shuffled **non-oracle** latent vectors and the trained intact one-step head. It includes six previous regression cases plus 12 new independently seeded blind cases by default; the independent set is disjoint in source magnitudes and has balanced sign and direct/composed families. For a positive outcome the intact head must lose no more than 1 correct answer relative to text-only, outperform zero and shuffled controls by at least 2 cases each, and be no worse than 1 parseable answer behind text-only. These are screening criteria, not a statistical claim of generalization. `--cases 6` is an abbreviated regression check; `--cases 60` or `--cases 120` is a larger independent evaluation at the cost of extra inference. Test results are not used to tune model weights.

## Files and reports

Defaults to the **new** lineage:

`C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_alignment_repair_v1`

- `calibration.json` — dev-only scalar calibration and embedding geometry
- `alignment/config.json`, `alignment/last.pt`, `alignment/best.pt`, `alignment/result.json` — reproducible, resumable head/gain training
- `geometry_gate.json` — test-free head quality gate
- `recurrence.json` — cached/full consistency and boundary tests
- `gate_result.json` — blind test outcomes, individual completions and independent-versus-regression breakdown

Training `--resume` is allowed only from a paused checkpoint with matching complete configuration and source hashes. For example:

```text
python -m tools.nanojev_qwen_hybrid_alignment_repair --stage train --device cuda --resume
```

The program never silently overwrites completed training output. Other stages load saved artifacts and refuse source mutations or altered fingerprints. A failed result returns a nonzero process exit code and sets `deeper_training_permitted: false` (when a final gate result exists). A partially completed pipeline is not certification.

## Research limitations

The upstream HybridLink includes an output LayerNorm and remains unchanged. This experiment adds only an **output gain at the Qwen embedding boundary**, because an output norm around `sqrt(1024)` is not necessarily in the native input embedding distribution. Scaling removes a major distributional error but does not create semantic competence. The teacher-forced embedding pair distribution is not the same as recurrent self-generated latent states; the blind gate checks that gap. Text-only accuracy on 6 cases was 6/6, intact was 1/6, zero was 5/6, and norm-matched was 4/6 in the preceding diagnostic. Nothing in this patch guarantees improvement over those values. If head geometry improves but blind utility does not, the correct status remains `ONE_STEP_FAIL`; do not authorize multi-step hybrid training or HybridRL.
