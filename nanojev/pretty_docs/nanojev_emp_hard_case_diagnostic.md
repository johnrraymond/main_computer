# NanoJev four-bank diagnostic — hard TRAIN examples

## Scope

This updates **`tools.nanojev_emp_dual_bank_diagnostic` only**. The four bank
weights, optimizers, training schedule, acceptance gate, and best-pair policy
are unchanged. The diagnostic remains read-only relative to the campaign. A
specialist model **is not trained** by this patch.

The default diagnostic still analyzes the frozen DEV decks. Opt in to a
separate `--mine-hard` experiment to generate **TRAIN-fold** operand pairs,
score all four banks on exactly those pairs, group failure patterns, and export
selected hard examples for a later specialist-head experiment.

**No sealed TEST examples are opened or exported, and DEV examples never enter
the hard-case training export.**

## Example: diagnose the current stage

Stop the running trainer before executing (the diagnostic checks checkpoint
hashes before and after inspection). From the Main Computer repository:

```powershell
& $py -m tools.nanojev_emp_dual_bank_diagnostic `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --radius 16384 `
    --compare-parent `
    --device cuda `
    --mine-hard `
    --hard-candidates 8192 `
    --hard-max-cases 512 `
    --hard-per-anchor 8 `
    --hard-min-failures 2 `
    --top 12
```

Replace `--radius` with the stage you want; omit it to inspect the most recent
checkpoint stage. 8192 cases is a diagnostic sample, not a minimum training
budget; if too few interesting cases appear, increase `--hard-candidates` or
use `--hard-min-failures 1`.

The run produces existing `EMP_DIAG_*` lines plus `EMP_DIAG_HARD_CASES`.
Unless overridden with `--report` and `--hard-export`, files are adjacent to,
not inside, the campaign root:

- `nanojev_emp_dual_bank_outward_v1_diagnostic_16384.json` — DEV diagnostic,
  with robust p50/p90/p99 absolute and relative errors in each bank summary,
  plus the TRAIN mining summary when enabled.
- `nanojev_emp_dual_bank_outward_v1_hard_train_16384.jsonl` — at most 512
  deduplicated hard **TRAIN** rows with exact operands, targets, four bank
  predictions, failure categories, bank disagreement, anchor and residual
  cohorts, checkpoint SHA and source split.

The miner also summarizes `all_four_wrong`, `both_selected_wrong`,
`three_or_more_wrong`, `two_or_more_wrong`, and failure counts for each bank
across operand, residual, sign, and digit cohorts. The two role candidates used
for ranking are the two distinct banks with the strongest current DEV minimum
accuracy; bank names/EMP ownership do not constrain either role.

Every hard export includes the exact checkpoint SHA. It does **not** save the
original training batches, which the trainer did not log. Instead it scans a
new **deterministic, reproducible TRAIN-only pool** sampled by the same
outward training generator. The pool is stable for a given radius and seed so
repeated diagnostics at later checkpoints can compare the same inputs.

## Reserved specialist-only families

The sampled original TRAIN fold is subdivided by SHA-256 of its operand
magnitude anchor. Four fifths of eligible anchor families are candidates for
hard-case mining; one fifth is reserved for a **future specialist-only
holdout**. The diagnostic neither scores nor exports the reserved partition.
A later specialist experiment can generate new cases for those reserved
anchors and evaluate transfer to unseen numerical families. The split is
reproducible with `specialist_partition(anchor, seed)` in the diagnostic.

These reserved examples are **not** an independent test of the already-trained
four backbones: the base ladder used the wider TRAIN fold before this additional
partition existed. They only become a valid specialist-only holdout if the
specialist is not itself trained on those reserved anchors. They also do not
replace the sealed main TEST split.

## What we can and cannot infer

- `relative_stddev` / `scale_bias` remain ordinary descriptive error statistics.
- The diagnostic additionally records robust median, p90, and p99 error and
  counts of cases whose relative error exceeds 100%.
- A hard case is one that fails **now**, at this particular checkpoint, not
  necessarily one that fails persistently across thousands of steps. Rerun
  the same deterministic pool at later checkpoints to test persistence.
- The specialist-head training code, sampling strategy, independent holdout
  examination, and any eventual router are **intentionally deferred**.

To keep checkpoint attribution sound, stop the trainer first. The command
refuses if its inspected checkpoint changes during the probe.
