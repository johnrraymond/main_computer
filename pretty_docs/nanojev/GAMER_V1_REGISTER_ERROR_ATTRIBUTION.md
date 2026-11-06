# GAMER V1 — Flat-register error attribution (diagnostic patch 1)

## Goal

Determine **which inaccurate public registers actually contribute to errors in
GAMER's deterministic computed answers**, including zero-line failures, wrong
signs, and interactions/cancellation among two incorrect registers. Do not
change the existing architecture or train Qwen, LoRA, table heads, game heads,
ridge register heads, or C0 Wide.

The verified flat-register v2 run already contains the necessary evidence:

- `dev_predictions.jsonl`: original learned-route game, final answer and target.
- `dev_register_predictions.csv`: per-case oracle and regressed public registers.
- `train_formula_templates.json`: exact formula templates compiled from **train**.
- `final_report.json`: source fingerprints and the saved 3.2008 development MAE.

No model checkpoint, GPU, feature capture, or additional epochs are needed.

## Command

From the Main Computer repository root, after applying the patch:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase flat-attribution `
  --run runtime\reports\gamer-v1\transition-005-flat-registers-0002
```

`--run` for **this diagnostic** is the *completed flat-register experiment
folder*, **not** the original `transition-005` source-run folder. You may
supply `--attribution-output NEW_DIRECTORY` to choose a new report directory;
otherwise output defaults to an unused sibling named
`transition-005-flat-registers-0002-attribution-0001` (or the next number).

The source experiment, its `flat_heads.pt`, the original LoRA and the C0 Wide
checkpoint all remain read-only. The diagnostic works on saved development
predictions and never re-encodes Qwen prompts.

## Mechanism

For every development case where the **model-predicted game** matches the
certified game, replay the saved numerical answer from **predicted** register
values. Require its result to equal `dev_predictions.jsonl`. Recompute the
same formula with **all oracle values**; require its answer to equal the
certified target. These two checks prevent a corrupt or mismatched run from
being silently analyzed.

Then repeat the formula with exactly one register replaced by its oracle value:

```
original_error = abs(f(predicted_registers) - certified_answer)
repair_gain(r) = original_error - abs(f(predicted_except_r, oracle_r) - certified_answer)
```

A positive repair gain means correcting the register helped. A **negative**
gain means it made the answer worse; this often signals cancellation among
multiple erroneous registers and must **not** be discarded.

Evaluate every unordered register pair with both values replaced:

```
extra_gain(r,s) = pair_gain(r,s) - repair_gain(r) - repair_gain(s)
```

The extra gain captures non-additivity from the game formula **or** the absolute
error metric, including cancellation. It is **not** a unique Shapley/causal
allocation and should not be interpreted as the standalone importance of a
register. Both raw pair recovery and extra recovery are retained.

For each substitution, also count changes in nonzero target **sign**,
exact-zero accuracy within `1e-6`, and zero accuracy within `0.1`. The report
retains separate zero, small-negative, small-positive, outer-negative, and
outer-positive cohorts. It does not declare zero-line mastery.

## Output

- `final_report.json`: replayed original MAE; true learned-route coverage;
  zero-line measurements; register/game rankings by consequential errors;
  pairwise joint repair and extra interaction; harmful interventions; worst
  individual cases; original source fingerprints.
- `case_attribution.csv`: original vs full-oracle replay for each case,
  routing status, sign/zero status, and the strongest possible single/pair repair.
- `register_interventions.csv`: the exact one-register counterfactual values,
  raw register errors, signed repair gain, sign and zero-line repairs/damage.
- `pair_interventions.csv`: every pairwise repair, gain, extra nonadditive gain,
  and impact on sign and zero accuracy.
- `events.jsonl`: progress and integrity audit.

If the learned game route is wrong, this diagnostic **does not pretend** that
oracle game routing was learned. It labels the case `ROUTE_MISMATCH` and skips
register attribution for that case. Invalid counterfactual computations (for
example division by near-zero) are counted as invalid, not silently scored as
successful repairs. Source artifacts are SHA-256 checked before and after.

Register rankings use the sum of marginal error changes divided by the number
of **attributable development cases**, so their values represent the potential
change in full development MAE from repairing that register across its cases.
This is a hypothetical intervention metric, not a measured model improvement.

## Next step and data discipline

Use the results to select which register and game **training examples** to
expand, but do not fit register coefficients to these development labels. Once
those labels guide targeted training, preserve an additional untouched
acceptance split for measuring real improvement and zero-line retention.

The result is strictly diagnostic: it does not promote, modify, or retrain any
models or dataset.
