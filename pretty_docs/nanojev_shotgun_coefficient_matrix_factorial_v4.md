# NanoJev Shotgun v4 — expression × matrix factorial diagnostic

## Objective

Investigate the nine HDB08 examples whose explicit arithmetic was correct
but became wrong when the payoff matrix was added. In the first v3 trace,
5:2:3 at k=±1,±8 repeatedly returned 80% of the expected answer. The v4
probe determines whether those answers track **the expression**, **the
irrelevant matrix**, **both**, or **only the presence of additional numbers**.

This is an **evaluation-only** test against the actual joint-trained LoRA,
not a new training curriculum and not a replacement of v3's frozen deck.

## Exact interventions

For every selected HDB08 frozen held-out scenario, compare eight conditions.
Original `products` and `products_with_matrix` prompts are byte-identical to
Shotgun v3. Everything else explicitly asks for an arithmetic expression,
whose exact result is verified by Python integers.

| Mode | Arithmetic expression | Matrix / context | Correct answer |
| --- | --- | --- | --- |
| products | E | None | y |
| products_with_matrix | E | Original matrix M0 | y |
| products_doubled | 2E | None | 2y |
| products_doubled_with_matrix | 2E | Original M0 | 2y |
| products_with_matrix_hh_shift | E | Only HH increased by +1 point (M1) | y |
| products_doubled_with_matrix_hh_shift | 2E | M1 | 2y |
| products_with_numeric_context | E | Same four payoff numbers, not described as a matrix | y |
| products_with_matrix_explicitly_ignored | E | M0 explicitly labelled background only | y |

The 2×2 E/2E × M0/M1 experiment isolates the two factors. HH+1 changes
**only a displayed payoff**, not the requested arithmetic. The correct answer
must be *invariant* under M0→M1 at a fixed expression. The 2E controls deliberately
have a different correct result and must never be counted as the same-target
question. At zero offset, both expressions evaluate to 0; magnitude-dependent
conclusions use nonzero cases. Reference-number context is an approximate
length/numeric-content control, **not** a token-count-matched control.

The factorial reports include numeric-output changes, exact response deltas,
correct/incorrect transitions, 2x2 interaction, and a per-scenario record of
all prompt strings, raw emissions, expected answers and signed errors.
A noncanonical `PROBLEM: -0` is explicitly labelled rather than silently
treated as a mathematical miscalculation.

## Live, read-only command

From the Main Computer checkout (with `$py` pointing to your established CUDA
Python environment):

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
  --stage shotgun-factorial --frontier --frontier-hdb --device cuda `
  --terminal-mode numeric `
  --stage1-checkpoint "$HOME\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "$HOME\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --output "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1" `
  --radii 0,1,2,3,4 --frontier-max-radius 64 `
  --operand-limit 1024 --max-rung 8
```

Do not supply `--focus`; the command never trains. Use the *same* output
root as your current joint run. A same-volume NTFS hardlink pins one complete
`working.pt` (the training writer atomically replaces that path). A concurrent
training run can continue, but two CUDA inference/training processes may compete
for GPU memory; for the cleanest test run the diagnostic while training is
paused at a saved checkpoint.

Look for `QWEN_SHOTGUN_FACTORIAL=` with the saved report path. The full report
is written to the isolated subdirectory:

```
<joint-workspace>/shotgun_factorial_rescores/factorial_*/shotgun_factorial_report.json
```

The hardlink is removed after inference. No checkpoint, optimizer state,
`progress.json`, v3 Shotgun deck, or history/audit file is written or updated.
Each result is bound to the exact checkpoint SHA256 and optimizer step.

## Hypothesis tests

- **Expression dependence:** E→2E at M0/M1. If the answer doubles, Qwen
  is at least tracking the supplied expression's magnitude, even when wrong.
- **Matrix dependence:** M0→M1 at fixed E. Any numeric output change proves
  sensitivity to an input declared irrelevant to the requested expression.
  It does *not* prove the model literally recomputes from matrix values.
- **Factor interaction:** compare the matrix effect at E and 2E. If the
  matrix effect changes with the expression, both sources affect the answer.
- **Extra-number interference:** compare E alone, E plus numeric-only context,
  and E plus labelled payoff matrix.
- **Explicit instruction:** compare matrix-present and matrix-present with an
  extra instruction to ignore matrix; this changes instruction text as well.

Only evidence from actual checkpoint inference can decide which mechanism
holds. The package's tests verify contracts using synthetic model outputs;
they do not claim a live GPU result.

## Compatibility and installation

This patch is anchored to **Shotgun operation-isolation v3**. It creates a new
`--stage shotgun-factorial` read-only entrypoint; existing `--stage run`, its
four training populations, weights, optimizer schedule, and default v3 Shotgun
full audit are unchanged. There is no checkpoint migration and no diagnostic
rebaseline. `new_patch.py` must report exact source verification. Do not use
`--allowfuzz` or apply to v2.

Run the new targeted unit tests and the existing joint suite before first live
use. The tests cover exact original v3 prompts, independent source variations,
paired numerical diagnostics, zero-packet classification, the actual hardlink
workflow, and no checkpoint mutation.
