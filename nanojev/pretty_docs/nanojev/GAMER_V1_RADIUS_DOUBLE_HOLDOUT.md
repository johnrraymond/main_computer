# GAMER V1 — Radius Double Holdout (Games AND Numerical Values)

Read-only experiment operating on the existing `head-100-radius` activation
capture. **Do not recapture Qwen or retrain C0.**

## Command

From the Main Computer repository root:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head-100-radius-double-holdout `
  --run runtime\reports\gamer-v1\transition-005-expanded-head-100-0001-radius-activations-0002
```

The command uses the existing 48-context, radius-4 `activations.npz`; it requires
neither GPU nor model weights. It creates a **new sibling** directory named
`...-radius-activations-0002-double-holdout-0001`, containing `final_report.json`
and `double_holdout_predictions.npz`. New runs receive new numbered directories.
Neither the capture, checkpoint, nor source corpus is modified.

Optional switches: `--radius-double-folds 4`, `--radius-double-rank 12`,
`--radius-double-controls 1`, `--radius-double-seed 47`,
`--radius-double-stages embedding_mean100,qwen_final_mean100,qwen_final_100,c0_input_norm,c0_hidden_1,c0_hidden_2,c0_hidden_3,c0_bottleneck,c0_answer`.

## Two disjoint-value experiments

| Direction | Train on NUMBERS | Test on NUMBERS |
|---|---|---|
| Even to odd | −4, −2, 0, +2, +4 | −3, −1, +1, +3 |
| Odd to even | −3, −1, +1, +3 | −4, −2, **0**, +2, +4 |

Both directions also hold out entire game IDs (four grouped game folds by
default). No held-out game shares a game ID with any training game; no
held-out numerical value was used for fitting any probe in that direction.

Each layer receives the **same** game and numeral splits, a training-only
supervised slope/curvature basis (rank at most 12), training-only feature
scaling, a linear ridge decoder, and an RBF decoder with fixed regularization.
The number of actual basis directions may differ by layer. The shuffled-label
null **re-fits the basis**, not just the final decoder, to avoid label leakage.

### Two distinct evaluation modes

**`inductive`** is the primary test. The mean, basis, scale, and readout are
fitted on training games and values only. The held-out game supplies only the
single input being predicted, not a zero anchor or other calibrating sample.

**`unlabeled_context_centered`** is a secondary, transductive diagnosis. It
subtracts the average of all held-out **unlabeled** values in each held-out
game. This does not leak labels but requires multiple inputs from that game,
so do not claim it measures one-shot numerical decoding. Because test values
are symmetric, this centering may artificially make a zero activation appear
near the origin. The **inductive zero MAE** is the meaningful zero test.

## Report interpretation

Read `summary.layer_comparison` for the cross-layer R² grid. In `per_stage`,
inspect `experiments.odd_to_even.modes.inductive.nonlinear.per_value.0.mae`
for recovery of a **numerical zero never seen by that fitted decoder**.
`retrained_shuffled_label_controls_nonlinear` is the negative control.

Compare `embedding_mean100` (a shared literal-token-identity control),
`qwen_final_mean100`, `qwen_final_100` (concatenated 102,400-D),
`c0_input_norm`, and each C0 hidden layer. Positive held-out R² **and** a
substantial advantage over the refitted shuffled-label control support
transfer of a numerical coordinate to unseen games/values. Embedding-level
success alone may still be token-shape generalization, not arithmetic.

A poor probe score cannot demonstrate deletion of information. The synthetic
radius interventions are not valid certified game-answer labels; zero
recovery is not the same as solving an original game with a zero answer.

## PowerShell results table

```powershell
$p = 'runtime\reports\gamer-v1\transition-005-expanded-head-100-0001-radius-activations-0002-double-holdout-0001\final_report.json'
$r = Get-Content $p -Raw | ConvertFrom-Json
$r.summary.layer_comparison.PSObject.Properties | ForEach-Object {
    [pscustomobject]@{
        Stage = $_.Name
        OddStrictR2 = $_.Value.even_to_odd.inductive.nonlinear
        EvenAndZeroStrictR2 = $_.Value.odd_to_even.inductive.nonlinear
        OddUnlabeledCenteredR2 = $_.Value.even_to_odd.unlabeled_context_centered.nonlinear
    }
} | Format-Table -AutoSize
```
