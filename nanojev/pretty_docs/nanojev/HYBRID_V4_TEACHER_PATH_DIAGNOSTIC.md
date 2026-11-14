# HybridAgent v4.2: teacher-path diagnostic

Purpose: diagnose why correct teacher arithmetic products fail to improve held-out X/Y.
This is a **read-only DEV probe**. It does not re-train, bypass the teacher gate,
modify original checkpoints, or consult TEST to select a fix.

Run after a failed v4.2 teacher bootstrap (which saves `teacher_best.pt`):

```text
python -m tools.nanojev_hawk_dove_hybrid_teacher_probe --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v4_distinct_shuffle" --device cuda
```

Writes `teacher_diagnostic_v1/report.json` in the run directory.

What the report distinguishes:

- **Mathematical availability**: reconstruct X/Y directly from four normalized
  visible products (within floating-point rounding), with no access to oracle labels at inference.
- **Readout and shortcut**: compare original Gaussian, trainable readout without
  thoughts, teacher-fed model, and its thought-only path (without the frozen anchor).
  Thought-only is a diagnostic intervention and was *not trained* in this configuration.
- **Readout sufficiency**: fit two tiny diagnostic ridge regressions on TRAIN only,
  one from raw products and one from the learned typed-token embeddings;
  evaluate each on DEV. These are external probes, not modified model weights.
- **Input use**: same-instance paired correct/shuffled/zero-token predictions,
  absolute X/Y changes, rescued and regressed instances, and hidden/update norms.
- **Gradient path**: whether numerical loss differentiates through teacher-token
  embeddings, thought-update weights, and numerical readout. Nonzero gradients
  demonstrate an available path, not that the model uses it beneficially.
- **Hard cases**: exact-zero and near-zero X/Y subsets.

Interpretation:

1. If product reconstruction fails, inspect the count/payoff ordering or scale.
2. If the raw-product ridge readout succeeds but the typed-token probe does not,
   investigate the encoder representation or its linear accessibility. Failure of
   a *linear* probe does not rule out a nonlinear decoder.
3. If the teacher input changes but predictions do not, investigate the attention
   integration, thought-update amplitude and decoder sensitivity.
4. If predictions change but errors do not improve, the path is active but the
   supervised objective has not taught it useful arithmetic.
5. If teacher-only output is poor, that **does not prove** arithmetic is
   unlearnable: this architecture's teacher-only route has not been trained
   separately from the reference anchor. It motivates a controlled teacher-only
   training/decoding experiment, rather than bypassing the utility gate.

The experiment's existing DEV/TEST matrices have been repeatedly inspected.
This probe is diagnostic and is not independent certification.
