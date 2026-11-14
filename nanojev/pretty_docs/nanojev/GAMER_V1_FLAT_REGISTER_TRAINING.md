# GAMER V1 — Flat register regression (repaired training, v2)

## Change rationale

The initial v1 flat AdamW register head regressed catastrophically on the
certified held-out data: 17.875 register MAE versus its train-mean 3.4985,
with 284.19 composed-answer MAE versus saved C0 Wide 6.0506. The table and
game classifiers both reached 1.0000, so the immediate failure was numerical
register extraction, not routing on that evaluation.

Two problems motivated this repair: only about 12 training samples per
independent `(game, register)` row, and masked AdamW weight rows capable of
moving due to historical momentum on batches with no relevant label.

## Fixed architecture

- A **flat multilabel table classifier** and a **flat game/formula classifier**
  continue to be trained with dense supervision via AdamW.
- Each public register is fitted by an **independent per-game/role ridge
  readout** of frozen LoRA features. This is algebraically equivalent to the
  earlier successful `register-oracle` ridge probe (the previous diagnostic
  reported 2.0515 MAE under oracle game/role routing).
- The same register role is also fitted through a **pooled ridge readout**
  across all games having that role. This uses every applicable training label.
  No label from an unrelated role is included. Two-fold cross-validation,
  strictly within the training split, selects the pooled readout for a
  `(game, register)` only when it improves raw-MAE by at least 3%; otherwise
  that output uses the per-game ridge.
- Ridge readouts are trained in closed form, not with AdamW. There are **no
  register optimizer moments**, inactive-row updates, or shared trainable
  encoder features. Table and game heads have labels on every case.
- The final numerical result is still **computed deterministically** from
  the predicted registers using a formula template derived from certified
  training cases. There is no learned final-answer head.

The train/dev split is preserved. The game and register labels are supervision
only. At inference the primary route uses the **predicted** game ID to select
formula and register outputs. The `oracle_game_route_control` explicitly uses
true game identity and is **not** a deployable score.

## Run

Apply the ZIP at repository root on top of the existing GAMER register-oracle
and flat-registers patches. It changes no production checkpoints or model
weights. Run the same command:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase flat-registers `
  --run runtime\reports\gamer-v1\transition-005 `
  --device cuda
```

The default 160 epochs now apply **only** to the table and game classifiers;
register ridge fitting is a single, separate closed-form training pass.
`--flat-lr` and `--flat-batch-size` affect only the classifiers.
Use `--flat-epochs 20` for a smoke test; the full run should still use the
same classifier controls for a fair comparison with v1.

The command writes a **new** `transition-005-flat-registers-0002` sibling
(or next available suffix), leaving `transition-005` and previous diagnostic
runs read-only.

## Measurements

`final_report.json` contains train and dev metrics for:

- Multilabel table-set classification and game/formula top-1 classification.
- Register MAE for the CV-selected readout, independent per-game ridge,
  all-games same-role pooled ridge, and training-mean baseline.
- Per-register training/development MAE, signed-log MAE, per-register
  selection, and original training/support counts.
- Final-answer MAE and coverage for **learned route**, **oracle game route
  control**, **per-game ridge learned route**, and **pooled-role learned route**.
- Zero/small-positive/small-negative/outer-negative/outer-positive final-answer
  cohorts and the saved source C0 Wide on the same certified development cases.

New files:

- `register_readout_selection.json` — 593 independent `(game, role)` decisions
  with training-CV errors and pooling support.
- `dev_register_groups.json` and `train_register_groups.json` — register-level
  error comparisons and whether pooled or per-game readout was chosen.
- `flat_heads.pt` — classifiers, all ridge coefficient matrices, role/slot
  mapping, normalization, deterministic templates, and source fingerprints.
- Existing `*_register_predictions.csv` files now include selected, per-game,
  pooled-role, and train-mean predictions with per-case error columns.

The initial generator-to-computation oracle must still verify all expected
final answers before any predicted registers are substituted.

## Limits and acceptance

A per-game ridge model fitting 12 labels is a **reproduction control**, not
proof of robust generalization. Pooled readouts may be less accurate for roles
with different meanings across games; the CV chooses conservatively, but
only held-out data can establish the outcome. Distinct role names may still
represent related quantities, which is a later experiment, not an excuse to
leak labels now.

Do not infer LoRA insufficiency from errors in these readouts alone. Do not
promote this experimental model until its learned-route result demonstrates
meaningful gains against the saved C0 Wide baseline, especially zero-line and
sign retention. Never report oracle-game routing as learned inference.

## Verification

Regression tests verify exact agreement with the old per-game ridge oracle;
train-only selection; unrelated-role parameter isolation; output independence
from changed development targets; deterministic formula execution; actual
certified staircase/checkpoint provenance; and source checkpoint immutability.
An all-146-game, 1,752-train-case mock-feature smoke test also exercises all
593 register slots and 196 public register roles. Mock-feature numerical
results are **not** claims about actual Qwen/LoRA performance.
