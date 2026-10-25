# HybridLink teacher-format precision experiment

The 4,000-step baseline passes 1,837/1,992 away-from-zero DEV instances but
0/904 near-zero nonzero and 0/176 exact-zero instances. Its **minimum tier**
strict joint-5 accuracy is 0.588542; the aggregate joint all-mask rate is
0.597982. These are distinct measures.

This patch adds **one independent experiment**, leaving the original
`tools.nanojev_hawk_dove_hybridlink_curriculum` and its checkpoints/gates
unchanged. Neither the reference Gaussian checkpoint nor the saved FORMAT
checkpoint can be overwritten through the experiment runner. The output
folder must be separate from the existing curriculum output.

## Head-only precision experiment (run first)

Run from `main_computer` in the Python environment containing Torch:

```text
python -m tools.nanojev_hawk_dove_hybridlink_precision --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --curriculum-output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_precision_head_v1" --device cuda --scope head --steps 2000 --probe-every 200 --batch-size 128
```

The head-only arm freezes the original backbone and all old heads. It trains
an **additional** X/Y regression residual and a learned binary zero detector.
No oracle X/Y or zero labels are inputs to the prediction path; they are
supervised outputs at training time. Zero predictions use a discrete gate, so
an accepted zero coordinate can be *exactly* zero.

If the head-only arm cannot improve near-zero classification, run a **separate**
output directory and `--scope backbone`: this permits the existing backbone
and input representations to learn together with the correction, while
maintaining auxiliary product prediction. Do not overwrite the head-only run.

To extend an arm, use `--resume --steps 4000` with exactly the original
arguments and the same output path. `--steps` always means the total target
step count, not additional steps. Resume restores optimizer, sampling RNG,
and Torch/CUDA RNG state.

## Interpretation

The runner prints `PRECISION_TRAIN` on every probe and a final
`PRECISION_DEV_GATE` containing:

- Minimum tier strict X/Y joint 5% performance, still gated at 80%.
- All-mask instance rates split among away-from-zero, near-zero nonzero,
  and exact-zero populations.
- Learned zero classifier true/false positives and false negatives on DEV.
- Baseline and correction performance, including ungated regression metrics.

`result.json`, `latest.pt`, and `best.pt` are written **only** in the new
precision output directory. The DEV-best model is retained even if a later
probe declines. Model selection is DEV-only; TEST is never evaluated.

This is a **teacher-path FORMAT experiment**. The teacher tokens are still
computed from exact visible products. A high accuracy score here does **not**
certify the model can autonomously generate HybridLink thoughts; it does not
promote or modify the original Phase 1 gate. Passing any experiment must be
followed by a separately reviewed integration and full phase verification.

No deterministic oracle product summation is used as the answer in this
experiment. The classifier is a learned input-dependent decision; it does not
receive the zero truth label at inference.

Run regression tests:

```text
python -m pytest -q tests/test_nanojev_hawk_dove_hybridlink_precision.py tests/test_nanojev_hawk_dove_hybridlink_curriculum.py tests/test_nanojev_hawk_dove_hybridlink_format_diagnostic.py
```
