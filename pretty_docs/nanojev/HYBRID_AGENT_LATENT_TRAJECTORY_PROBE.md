# HybridAgent latent trajectory / feedback causality probe

This is a **read-only diagnostic** for the trained NanoJev Phase 1/2 HybridAgent.
It loads **best.pt** at the previously selected step and depth, verifies its
identity against the original frozen Gaussian checkpoint and the finalized run
report, and reconstructs the exact oracle-blind DEV payoff-matrix split.

No weights, optimizer state, run manifest or previous report are modified.
The probe creates a new output folder (`probe_v1/` by default). It refuses to
overwrite that folder. The original historical DEV/TEST have been examined;
all findings are exploratory rather than blind certification.

## Run

From the `main_computer` root, in the same environment as the training run:

```text
python -m tools.nanojev_hawk_dove_gaussian_hybrid_probe --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v1" --device cuda
```

Default: 128 presentations per batch, `--split dev`, 24 representative examples,
5% strict relative error and `--output <run-dir>/probe_v1`.
For a second probe, choose a **new** `--output` directory. To inspect TEST
without re-selecting a checkpoint, specify `--split test` and a distinct output
folder. No training or promotion takes place.

## What it measures

- Traces actual hidden states and generated HybridLink feedback at **every**
  depth 0 through 8, including mean hidden-step L2/cosine, feedback L2,
  feedback/input-embedding ratio, and differences between successive feedback
  vectors. A change in norm alone does not establish useful reasoning.
- Decodes X/Y from each depth using the original frozen Gaussian output path,
  and compares per-output error, exact-zero hits, full-presentation joint
  accuracy and **every-mask per-instance** joint accuracy for T01B–T08.
- Counts correct predictions that become wrong (**regressions**), initially
  wrong predictions corrected (**rescues**), and changes in total X/Y error.
  This distinguishes a flat aggregate metric from per-example movement.
- Tests **normal**, **disabled**, **shuffled**, **frozen-first-vector**,
  **half-amplitude**, and **double-amplitude** feedback at the selected depth.
  Shuffling deterministically rolls feedback by one row **within the batch**;
  keep `--batch-size` constant when comparing runs.
- Verifies that the instrumented trace exactly reproduces the existing model
  forward path at selected depths, and that DEV certification agrees with the
  saved original report. If identity or score differs, the probe refuses.

The `frozen_first` intervention feeds the **same first generated vector**
through every subsequent iteration. If normal and frozen-first predictions
are nearly indistinguishable, then ongoing *generation of new vectors* may not
be contributing meaningful computation, even if feedback matters at depth one.

## Files

`report.json`: source identities, depth scores, per-step norms, improvement and
regression counts, perturbation comparisons, and representative cases.

`trajectories.jsonl`: one record per presentation with the true X/Y, **decoded
X/Y at every depth 0..8**, and per-step feedback/hidden-state trajectory
statistics. Values are included for analysis only; true X/Y never enter the
model inference input.

## Reading the result

- Hidden vectors barely change and X/Y is stable: likely a fixed point.
- Hidden vectors change significantly but X/Y is stable: frozen decoder may
  be insensitive to the latent trajectory.
- Normal equals disabled across individual predictions: weak evidence that
  feedback contributes little to those outputs.
- Normal differs from disabled, but rescue and regression rates balance:
  feedback affects predictions without demonstrable benefit.
- Normal beats both disabled and frozen-first on held-out cases with net
  rescues: evidence for useful repeatedly generated feedback, though further
  unseen-matrix certification is required.

The probe does not attempt to retrain or fix the model, and does not implement
Phase 3's learned THINK/ANSWER policy.

## Tests

```text
python -m pytest -q tests/test_nanojev_hawk_dove_gaussian_hybrid_probe.py
```
