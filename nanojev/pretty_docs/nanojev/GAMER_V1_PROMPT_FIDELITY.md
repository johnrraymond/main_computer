# GAMER V1 — Prompt Fidelity and Exact Register Acquisition

## Purpose

`flat-prompt` is a read-only, template-bounded diagnostic for the existing
flat-register experiment. It determines whether the public register values
already written in the original `prompt_08` can be acquired *exactly* rather
than approximated through the frozen LoRA final-token activation vector.

It does **not** train, promote, or modify Qwen, LoRA, C0 Wide, flat heads, or the
existing certified game corpus.

## Command

```powershell
& $py -m tools.nanojev_gamer_v1_staircase flat-prompt `
  --run runtime\reports\gamer-v1\transition-005-flat-registers-0002
```

Optional `--prompt-output <new-empty-path>` selects a new isolated report
location; otherwise a unique sibling directory is used.

The completed `flat-registers` v2 report, training-derived formula plans,
`dev_predictions.jsonl`, `dev_register_predictions.csv`, and the original
source run's `certified_pairs.jsonl` must be present. Their SHA256 provenance
is validated. The command does not capture Qwen features or rerun training.

## Diagnostic routes

* **Ridge**: already-saved model-selected game ID, regressed public registers,
  deterministic executor; reproduces the previous 3.2008 dev MAE on the
  user's current completed run (expected, not precomputed by this patch).
* **Exact**: the **saved predicted game ID**, its **train-compiled formula**, and
  **only the original prompt text** enter extraction. Public original prompt
  templates bind each numeric token to its named register (including both
  players' matrix payoff cells and the public opponent action choice). Exact
  rational arithmetic executes the selected plan. Oracle/source register
  values are used only afterward to evaluate fidelity.
* **Hybrid**: use exact values when all required registers have been acquired
  and their computation validated; otherwise preserve the existing saved ridge
  output. No silent oracle substitution or guessed repair is allowed.

The output separates arithmetic cancellation from matrix action-index zero,
reports exact and signed register errors, and flags fractional discrete
quantities, suspicious long decimal literals, and near-zero-but-nonzero labels.

Artifacts: `final_report.json`, `case_comparison.csv`,
`register_comparison.csv`, `prompt_anomalies.csv`, and
`corpus_prompt_anomalies.csv` (all source corpus rows, not only dev).

## What it proves and what it does not

This is a test of how much accuracy is lost by unnecessarily *regressing*
values already explicitly written in these particular source-authored prompt
templates. It is **not** an independent demonstration that a neural model can
extract register identities from unseen free-form language. The parser knows
our public text templates and the formula routing still comes from the saved
game classifier. Any route mismatch is measured as such; no hidden game ID
or hidden generator parameter is used to correct it.

For zero-boundary labels, the diagnostic does not threshold answers to zero.
The exact interpreter distinguishes true rational zero from a literal such as
`0.2599999999999998` which can lead to a tiny nonzero answer.

## Optional V2 scalar generator — future datasets only

The generator now offers `pairs.generate_case(game, index, seed,
 prompt_fidelity_v2=True)` for **original scalar games**. It preserves integer
crew/agent/vote counts and constructs selected arithmetic boundary inputs with
exact decimal arithmetic before formatting. Cases use distinct
`PROMPT_FIDELITY_SCALAR_SCHEMA` and `prompt_fidelity_signature()` provenance,
with `pairs.certify()` replaying their variant. The default generator remains
bit-for-bit compatible with previously certified rows. Existing datasets are
never rewritten by this diagnostic. V2 generation is an opt-in building block
for the *next* corpus version, not an implicit promotion or change to training.

## Acceptance

The diagnostic requires complete saved source evidence and checks replayed
ridge MAE against the original completed run. It reports exact extraction
coverage, per-register equality to independently certified oracle values,
and answer MAE/zero/sign metrics for each route separately. Do not infer
production generalization from template-bound extraction accuracy alone.
