# Shotgun v5 — ten new controlled HDB08 prompt formulations

## Goal

Find better ways to express *the actual failed HDB08 mathematical problems*, rather than training on generic arithmetic. Rephrase the **same** fixed payoff matrix, encounter counts, signed coefficient products and half-point answer. Keep every previous HDB08/Shotgun v3 control unchanged, including its exact output format.

## New evaluation formulations

| Mode | Representation / distinction |
| --- | --- |
| `direct_instruction` | Clear numerical instructions with payoff reference |
| `spreadsheet` | Two human-readable tables: payoff values and term quantities |
| `json_packet` | Machine-readable object with numerical payoff data and products |
| `math_notation` | Matrix M, variables and sum-of-products expression |
| `accounting_ledger` | Signed transactional contributions |
| `pseudocode` | Executable-looking integer arithmetic with explicit variables |
| `verbal_word_problem` | Payoffs, counts and coefficients written in words |
| `arithmetic_first` | Requested computation before payoff context |
| `io_contract` | Formal function/input/output specification |
| `separated_context` | Explicit difference between contextual matrix and requested expression |

These ten modes are **diagnostic-only** at this stage. They are generated for the same held-out HDB08 problem instances as the pre-existing 13 modes; no new mathematical targets, payoff families, or held-out-training contamination. The frozen evaluation deck is now **23 modes × 18 matched cases = 414 prompts** at the user's `--joint-shotgun-eval-per-mode 6` setting (a smaller configured deck may contain fewer HDB08 offsets). All modes for a scenario require precisely the same `PROBLEM: <integer>` target.

The 13 older modes remain byte-for-byte equivalent to v4. The original `prose` mode remains the verbatim HDB08 prompt and serves as control. `shotgun_matched_comparison.against_prose_payoff_matrix` reports for each new mode when a rephrasing rescues an original-prompt failure and when it loses a previously correct answer. `shotgun_balance_error.by_mode` reports exact score, numeric emissions and normalized residual errors. Full prompts and raw model emissions remain recorded in `shotgun_operation_cases.json`.

## Existing-training integration and migration

No changes to the four joint optimizer populations, mode sampler, focus weights, gradients, learning rate, LoRA, existing T/HDB/mathematics decks, or normal training command. The ten new modes are *not* selected for training before their comparative diagnostic has been scored. At the next restart from the existing v3 checkpoint:

- verify the old 234-case deck fingerprint;
- preserve the entire original checkpoint as `pre_shotgun_rephrase_neighborhood_migration.pt`;
- archive the previous v3 diagnostic and 234-case evaluation deck as `shotgun_v3_archived_diagnostic.json` and `shotgun_v3_eval_deck.json`;
- establish a separately versioned 414-case baseline **at the same optimizer step, before any new optimizer step**;
- continue the existing training run with the same parameters and optimizer state.

The new baseline must not be compared numerically against old scores as though the deck were unchanged. The full 414-prompt pass adds inference time to a full audit. It does not change update frequency or training batch composition.

## Run

1. Stop after a successful saved checkpoint; run your existing `new_patch.py` installer with `--dry-run`, then without it.
2. Relaunch your unchanged long-running command, including `--joint-shotgun-eval-per-mode 6`.
3. Inspect the `QWEN_JOINT_SHOTGUN_DIAGNOSTIC_MIGRATION` event. It should report 414 matched cases and the saved checkpoint fingerprint and preservation status.
4. Inspect `QWEN_JOINT_FRONTIER_CHECK.shotgun_balance_error.by_mode`, `shotgun_matched_comparison.against_prose_payoff_matrix`, and `shotgun_operation_cases.json`.

A concise PowerShell extraction from the latest full audit:

```powershell
$root = "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1_aggressive_frontier_v1_joint_t_hdb_numeric_frontier_v2"
$r = Get-Content "$root\joint_frontier_report.json" -Raw | ConvertFrom-Json
$r.shotgun.normalized_balance_error.by_mode.PSObject.Properties |
  Where-Object { $_.Name -in @('direct_instruction','spreadsheet','json_packet','math_notation','accounting_ledger','pseudocode','verbal_word_problem','arithmetic_first','io_contract','separated_context') } |
  ForEach-Object { [pscustomobject]@{Mode=$_.Name; Exact=$_.Value.exact_correct_numeric; Tested=$_.Value.tested; MedianError=$_.Value.median_absolute_error_offset_units} } |
  Sort-Object Exact -Descending | Format-Table -AutoSize
```

If the full report is not yet available, use the model's emitted `QWEN_JOINT_FRONTIER_CHECK` record and the saved operation trace after migration.

## Verification

- 21 targeted tests cover correct target generation, prompt uniqueness, verbal encoding, typed JSON, training/evaluation separation, policy compatibility and matched mode comparisons.
- 39 broader NanoJev joint-frontier/Shotgun tests pass in the local Python environment.
- The 234 pre-existing held-out prompts and all their metadata were verified byte-for-byte equivalent to the previous v4 source.
- No CUDA inference was performed against the user's real checkpoint here. The ten modes' actual success rates remain unknown until migration/evaluation.
