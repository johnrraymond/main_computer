# Qwen greedy failure-mode glass — from-scratch memorization experiment

## Goal

Start a NEW glass (not the existing `glass.pt` or `glass_refinement`),
freeze the fully trained Qwen backbone **and** its LM head, recover the
original failed prompts from the **frozen** `baseline.pt`, and train only
this new glass until each initially failed prompt greedily produces its
correct answer. The initial acceptance inventory is **54 known failures**.
No generalization or shared-mode compression is required for this test.

**The correct answer is a training label, never an inference input.**
During both training and deployment, mode matching sees only Qwen's
live internal activations. Token logits are used **only** to calculate
training loss and choose the next greedy token. They are **not** glass inputs.

## How the new glass differs from the old one

- Observe a normalized, full-width multi-layer activation signature
  sampled at four depths through the *frozen* Qwen transformer. Frozen
  weight matrices are already embodied in these activations; static weights
  alone cannot identify which example is failing.
- Store a separate zero-initialized 1,024-dimensional (or actual Qwen
  hidden width) correction vector per newly observed failure state.
- At inference, perform strict activation matching and add the correction
  to Qwen's last hidden vector **before** its existing frozen LM head.
  Unknown activation states receive exactly zero correction.
- During training, run the **real autoregressive greedy output**. When it
  first disagrees with the target token, backpropagate a positive-margin
  loss through the frozen head into **only that correction vector**.
  Regenerate after every successful token correction and continue to the
  next wrong token, if any.
- Recheck **all** known failed prompts after every pass. Save progress after
  completed cases. Exit nonzero if the pass budget runs out before all are
  fixed. Never report 12/54 or 42/54 as success.
- A separate complete-corpus audit checks for regressions among the
  originally correct cases. The training corpus is not evaluated repeatedly
  during each targeted correction; the full audit is a separate command.

## Prerequisites

The original two-pass experiment must already have created:
`<output>/baseline.pt`, `<output>/glass.pt`, `<output>/source_snapshot.pt`,
`<output>/report.json`. The original glass is read only for parent-checkpoint
validation; **the new glass starts empty**. This module does not edit
these files or the concurrently running ladder's `working.pt`.

`$glassArgs` is the same array used by `nanojev_qwen_lm_deformation_smoke`.

## Step 1: Recover ONLY the frozen model's failed prompts

```powershell
& $py -m tools.nanojev_qwen_lm_failure_mode_glass `
    --stage harvest @glassArgs
```

The first full greedy scan saves the input-only prompt list in:

`<output>/glass_failure_modes_v1/known_failure_prompts.txt`

The corresponding training inventory (source, desired target, wrong baseline
output, rung, residual, id) is saved in `known_failures.json`. It must contain
54 entries by default; if the reproduced count differs, the file is saved
for inspection but training stops with an explicit error. The scan is
restartable via `harvest_partial.json`.

## Step 2: Train ONLY the fresh glass until all known failures are fixed

```powershell
& $py -m tools.nanojev_qwen_lm_failure_mode_glass `
    --stage train @glassArgs
```

Training consumes only the **known failed prompts** after harvesting. It
checks full generated answers after each update. Default guardrails:

- up to 100 correction passes (normally stops much earlier if successful),
- up to 20 successive token repairs per prompt per pass,
- 100 optimizer steps to make the desired token beat every competitor by a
  margin of 2.0,
- cosine activation matching threshold 0.99999,
- checkpoint every five failed prompts.

The authoritative checkpoint is
`<output>/glass_failure_modes_v1/working.pt`; partial training is in
`partial.pt`. Repeating the command resumes saved state. The log line
`FAILURE_GLASS_PASS` reports `correct`, `remaining`, and `modes`.
If the training budget is exhausted with errors remaining, it emits
`FAILURE_GLASS_INCOMPLETE`, saves the offending examples, and exits with an
error rather than claiming completion. Raising `--max-passes` resumes.

## Step 3: Complete-corpus regression audit

```powershell
& $py -m tools.nanojev_qwen_lm_failure_mode_glass `
    --stage audit @glassArgs
```

This regenerates all 20,149 answers using the learned glass. The final report
`glass_failure_modes_v1/full_audit.json` includes every still-failing case,
the count of known failures not yet fixed, and the count of newly introduced
errors. For full smoke-test acceptance require `failed == 0` and
`new_errors == 0` and `known_failures_unfixed == 0`.

## Optional direct inference

```powershell
& $py -m tools.nanojev_qwen_lm_failure_mode_glass `
    --stage infer @glassArgs `
    --prompt 'PROBLEM: 6+(-6)'
```

Inference supplies no target and no gold token IDs. It uses the frozen
backbone, its live activations, the learned mode keys, and the frozen LM head.

## Limitations

This is a deterministic, finite-corpus memorization experiment, **not** a
proof that unseen arithmetic is correct. Exact-zero behavior applies to
unmatched activation signatures, not to nearby signatures that match a mode;
a full regression audit remains required. In pathological cases, different
required tokens may be indistinguishable to the chosen activation sampler:
the trainer reports unresolved failures rather than pretending a correction
exists. More sampling or corrective capacity may be needed only if that
happens on the actual 54 known failures.
