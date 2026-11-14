# Shotgun v2 — failure-neighborhood differential probes

Source snapshot: `main_computer_test-20261102-114919(2).zip`.

## Purpose

The prior joint Shotgun trained generic signed balances and evaluated 48 prompts over only six underlying calculations. Its eventual 48/48 was not a diagnostic of HDB08 failure. Shotgun v2 replaces the **joint trainer's Shotgun sample generator and frozen comparison deck** with matched variants of real HDB08 payoff-matrix problems. It does **not** implement activation geometry, a separate optimizer, or a fifth training population.

## Comparison contract

Each of the three heldout HDB08 payoff matrices is evaluated on the same selected encounter offsets in eight representations; the exact expected terminal is identical within the group:

- `products` — derived signed half-point products supplied, analogous to HDB07
- `contributions` — evaluated products supplied, only signed addition remains
- `intermediate` — payoff matrix and converted coefficients both supplied
- `structured` — named raw payoff values and counts, no converted coefficients
- `table` — raw payoff matrix as a compact 2×2 table
- `prose` — **verbatim original HDB08 prompt** for the one-million-encounter case
- `reordered_prose` — same raw payoffs in reverse order
- `word_problem` — same raw payoff relation in narrative form

The default `--joint-shotgun-eval-per-mode 6` now means six offset values **per payoff matrix and representation**, 3×6×8 = 144 prompts on the usual nine-offset HDB evaluation policy. If the saved HDB evaluation policy has fewer offsets, the diagnostic only uses offsets actually on that frozen deck. Existing HDB/T/math evaluation files are unchanged.

The report includes `shotgun.matched_hdb08_comparison` with pairwise counts of cases in which each other representation succeeds while original HDB08 fails, and the converse, plus outcomes by payoff matrix and offset. It also contains error distributions by representation, offset, and payoff matrix.

## How training responds to failures

The joint trainer still assigns a mandatory Shotgun example per optimizer step. For its HDB08 training example, it derives a *failure profile* from the most recent full HDB08 diagnostic. The profile contains only failure offset categories and sign counts, **not heldout payoff matrices, prompts, or target answers**. Seventy-five percent of new Shotgun examples draw from those failing offset categories; the other quarter samples broad offsets from zero out to ±1,024. Independently drawn train-only payoff matrices use common multipliers 1–4; heldout matrices use multipliers 5, 7, 9. Training also varies the total encounter counts among 100,000; 200,000; 1 million; 2 million; and 10 million. Every generated answer is computed exactly and checked against the algebraic oracle. Training continues to sample all eight presentations, with the existing weak-mode priority logic unchanged.

The failure profile is frozen between full audits, then updated at the next one, so the experimental priority has a stable audit interval. Focus flags retain their prior meanings. This first version targets **HDB08** as the disputed hard case. HDB04, other mathematics families and T chains remain separate, unchanged training populations and remain in the standard audits; they are **not yet** controlled Shotgun neighborhoods.

## Preservation-safe resume

Stop the live trainer after its next lightweight checkpoint; install the patch using `new_patch.py` and restart the **same command**.

On its first restart, `QWEN_JOINT_SHOTGUN_DIAGNOSTIC_MIGRATION` is emitted after a baseline evaluation, with:

- An immutable `pre_shotgun_failure_neighborhood_migration.pt` copy of the existing checkpoint, including optimizer and LoRA.
- An archived `shotgun_v1_archived_diagnostic.json` recording the old 48-case score and counts.
- A new `shotgun_eval_deck.json` with the matched HDB08 cases and a new deck fingerprint.
- A new baseline and error distribution for Shotgun v2, without calling the optimizer.
- Continued T/HDB/math decks and counters, existing Shotgun exposure counters, focus schedule and AdamW state.

Never compare old 48/48 directly with the new 144-case score. If reading `--stage competency` immediately after migration, Shotgun is labeled as a *new baseline only* until the next full audit.

## Commands

Keep the original run command; `--joint-shotgun-eval-per-mode 6` may remain unchanged. Do **not** use `--allowfuzz` to force this patch onto a different source. After the first completed audit, inspect:

```powershell
$root = "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1_aggressive_frontier_v1_joint_t_hdb_numeric_frontier_v2"
$r = Get-Content "$root\joint_frontier_report.json" -Raw | ConvertFrom-Json
$r.shotgun.matched_hdb08_comparison | ConvertTo-Json -Depth 20
$r.shotgun.targeted_failure_profile | ConvertTo-Json -Depth 10
$r.shotgun.from_baseline.by_mode | ConvertTo-Json -Depth 10
```

Interpretation examples: `products` correct while `prose` wrong suggests difficulty occurs before executing the explicit products, such as extracting the raw payoffs; both wrong suggests the numerical operation or an inference-format issue is still unresolved. These are controlled *descriptive* tests, not causal conclusions from gradients or hidden activations.

## Tests

Run:

```powershell
python -m pytest -q tests/test_nanojev_qwen_joint_frontier.py tests/test_nanojev_qwen_shotgun_joint.py
```

Tests cover exact oracle parity across modes, pairing against the original HDB08, disjoint train-only matrices, failure-profile responsiveness, four mandatory batch populations, policy drift refusal, archived v1 migration preserving optimizer and LoRA, saved diagnostics, and new baseline semantics. Live CUDA behavior still needs verification on the user's hardware.
