# Shotgun v3 — controlled operation-isolation diagnostic

## Objective

Investigate the actual HDB08 failures in the long-running four-population
Qwen numerical-terminal run. Hold a mathematical scenario fixed and vary one
computational requirement or piece of surrounding context at a time. Identify
paired pass/fail flips rather than assuming that repeated training exposure
identifies the failing operation.

## Scope and preservation

`tools.nanojev_qwen_shotgun.MODES` remains the **eight existing training modes**.
`DIAGNOSTIC_MODES` includes those eight plus five *evaluation-only* modes.
Optimizer sampling, LoRA state, checkpoint step, T/HDB/math decks, and the
training matrix exclusion policy are unchanged. There is no separate
optimizer, no background process, no new standalone migration utility, and no
new source of supervision from the held-out examples.

At the default `--joint-shotgun-eval-per-mode 6`, the frozen evaluation is
3 held-out payoff matrices × 6 selected offsets × 13 representations = **234
prompt evaluations**, describing **18 different exact computations**.
This replaces v2's 144 evaluations and increases audit time. An audit is
not live progress until `QWEN_JOINT_FRONTIER_CHECK` is committed.

The `prose` mode is the canonical, byte-exact original HDB08 prompt.
All modes for a scenario have the same exact `PROBLEM: <signed integer>` target.

## Five new probes

For a representative case with Hawk/Dove half-point coefficients -10 and +15,
and counts 600001 and 399999, the correct output is `PROBLEM: -25`.

| Mode | Example/question | Isolated change |
| --- | --- | --- |
| `products` (existing) | `In half-point units compute exactly: (-10*600001)+(15*399999).` | Explicit calculation, no payoff matrix |
| `products_with_matrix` | Identical `products` sentence with original payoff matrix prepended | Added payoff context while preserving the exact expression |
| `point_differences` | Each opponent-H encounter gives -5 points; each opponent-D encounter gives +7.5 points; counts provided | Convert points to half-point units and combine, without deriving payoff differences |
| `point_differences_with_matrix` | Identical `point_differences` sentence with payoff matrix prepended | Added payoff context with derived point differences already supplied |
| `points_expression` | `2*((-5*600001)+(7.5*399999))` | Explicit arithmetic in point units, requiring ×2 conversion |
| `payoff_subtractions` | `2*(((-5)-(0))*600001+((15)-(7.5))*399999)` | Explicit payoff subtraction embedded in arithmetic, no matrix-reading instruction |
| `prose` (existing) | Original HDB08 payoff-matrix question | Derive differences, convert units, multiply, cancel, emit exact result |

The five *additional* modes are `products_with_matrix`, `point_differences`,
`point_differences_with_matrix`, `points_expression`, and `payoff_subtractions`.
The old word-representation controls remain available.

## Outputs

Each new audit reports `shotgun.matched_hdb08_comparison.operation_boundaries`,
including paired `both_correct`, `left_correct_right_wrong`,
`left_wrong_right_correct`, `both_wrong`, and `tested`. Each contrast reports
all matched cases and separately reports **nonzero** cases, preventing the
three easy zero-balance cases from obscuring numerical failures.

`shotgun_operation_cases.json` contains the prompt, actual greedy output,
parsed numerical answer, signed error, expected answer, scenario offset,
payoff matrix key, and audit step for **every case**. It is never read into
the training batch or used as a teaching target. Consult the file to see
exactly where a model answer becomes wrong.

Controlled differences are diagnostic evidence of a failure boundary, not
proof of a single internal cause. For example, failure only after adding the
matrix is evidence of context sensitivity, not proof of a specific neural
representation problem. Tests that also remove a precomputed expression
change two factors; interpret those contrasts more cautiously.

## Safe migration from v2

1. Allow the current trainer to save `working.pt`, then stop it.
2. Apply the patch with `new_patch.py` (dry run first, no `--allowfuzz`).
3. Restart with the existing `--stage run --frontier --frontier-hdb` command.
4. At startup the trainer copies the v2 working checkpoint to
   `pre_shotgun_operation_isolation_migration.pt`, saves the old
   `shotgun_v2_eval_deck.json` and `shotgun_v2_archived_diagnostic.json`,
   evaluates the v3 baseline **without optimizer steps**, and persists the
   v3 frozen deck and raw case evidence.
5. The next regular full audit prints the comparison with newly measured
   model outputs; `--stage competency` only reads the latest completed audit.

The original eight Shotgun *training* modes remain active in the
four-population optimizer. The new five modes are diagnostic-only until
specific failures justify a deliberate training-design change.
