# Shotgun v6 — four new HDB08 prompt reformulations

## Motivation

At the v5 baseline, `accounting_ledger` and `separated_context` were the strongest new non-arithmetic-only formulations (7/18 each) versus original `prose` (5/18). Expand **only those two representational neighborhoods**, on the same heldout mathematical scenarios, while keeping the existing optimizer untouched.

## New diagnostic-only modes

| Mode | Experiment |
|---|---|
| `ledger_reconciliation` | Treat signed contributions as two separate accounts, calculate signed subtotals, then reconcile |
| `debit_credit` | Convert negative entries to debit totals and positive entries to credit totals, net as credits minus debits |
| `numbered_operations` | Separate payoff reference from three numbered arithmetic operations |
| `authoritative_calculation` | Designate the already-derived expression as authoritative, separate from payoff reference |

Each prompt contains the original four game payoffs in points, the same two encounter counts, the same signed integer half-point coefficients, and the same exact signed answer. The original HDB08 prose prompt remains verbatim and unchanged. All 414 v5 diagnostic prompts and their metadata remain unchanged.

These four modes are **evaluation-only**: no mode is added to `MODES`, no training sampler change, no optimizer or LoRA change, no change to T/HDB/mathematics populations or focus weights. The updated diagnostic is 27 modes × 18 heldout HDB08 mathematical scenarios = **486** prompts with `--joint-shotgun-eval-per-mode 6`. If the HDB eval configuration exposes fewer than six offset cases per matrix, the deck size scales with actual HDB coverage (for example 27×15 = 405).

## Safe checkpoint migration

- Recognizes frozen v5 policy `nanojev.qwen.shotgun.rephrase-neighborhood.v5` as a diagnostic-only predecessor; all training policy fields still must match.
- Preserves checkpoint at `pre_shotgun_ledger_context_migration.pt`.
- Verifies source v5 deck fingerprint, then archives it as `shotgun_v5_eval_deck.json`, with summary in `shotgun_v5_archived_diagnostic.json`.
- At identical model/optimizer weights, scores the expanded v6 deck and writes baseline to `shotgun_operation_cases.json`, `shotgun_eval_deck.json`, and the working checkpoint without incrementing optimizer step.
- Existing historical v1/v2/v3 migration branches remain intact.

## Run

Apply the patch zip to a saved v5 repository version with the project's `new_patch.py` tool. First dry-run, then apply. Resume the user's existing, **unchanged** four-population training command with `--joint-shotgun-eval-per-mode 6`. The initial migration emits `QWEN_SHOTGUN_PROGRESS` up to 486 and `QWEN_JOINT_SHOTGUN_DIAGNOSTIC_MIGRATION` with the new diagnostic version. This is inference, not training.

Read `progress.json` at `shotgun_current.by_mode` to rank the four new representations. The file `shotgun_operation_cases.json` records matched per-scenario source prompts, raw emitted replies and signed numerical errors. Use `shotgun_matched_current.against_prose_payoff_matrix` to distinguish actual rescues of original HDB08 failures from absolute mode accuracy.

## Test coverage

41 targeted/adjacent CPU tests pass, including explicit zero-answer leakage checks, same-scenario exact balance, all previous heldout prompts matching frozen hashes for both 5-offset and 6-offset configurations, no new optimizer training modes, and a mock v5-to-v6 migration preserving the original saved v5 deck, checkpoint LoRA tensor and optimizer state. The old tests for the original v3/v2 migration, batch composition, checkpoint resume, and factorial diagnostics still pass.

No inference against the user's live CUDA checkpoint has been performed by this patch builder.
