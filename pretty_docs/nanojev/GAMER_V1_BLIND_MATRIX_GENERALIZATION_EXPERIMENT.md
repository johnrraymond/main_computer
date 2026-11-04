# Gamer v1 — anonymous 2×2 matrix generalization experiment

## Objective

Test whether Qwen LoRA can translate an original, human-readable *08-like payoff narrative into a complete, numerically populated mathematical payoff table for **previously unseen payoff matrices**, without receiving the game name or recognizing the source game's characteristic pattern. The numerical `to_numerical` head still consumes only hidden activations of the original narrative.

This is a **separate, opt-in experimental population** in Gamer v1. It does not replace the 33 source-verified game models, the currently running training dataset, or any existing checkpoints.

## Source-grounded defect motivating the experiment

The v6 `sample_matrix` procedure derives a candidate payoff matrix by multiplying source game payoffs by a positive factor and adding player-specific constants. It then overwrites one active payoff with a −1/0/+1 advantage. That checks local invariance, but can preserve game-specific relationships, action names, and shortcut patterns. Merely shuffling the existing 33 game instances cannot establish blind generalization.

## Training matrix construction

The opt-in `--blind-matrix-per-game N` flag adds N anonymous 2×2 cases per each of the 14 oracle-backed matrix models; N must be a nonnegative multiple of six. The existing 33-game base dataset is retained. Source game IDs serve *only* as provenance and independent deterministic sampling keys. They do **not** appear in either the original prompt or the text supervision target.

Each new 2×2 payoff tensor is generated **independently of the source game's payoff tensor**. Blind regimes are: independent random payoffs; zero-sum payoffs; symmetric payoffs; coordination; anti-coordination; exact ties; signed-neighbor advantages; large cancellation between nearly equal payoffs; and positive affine transformations of random matrices. Row and column action order are independently permuted. The opponent's selected move is tracked across column permutation. Both players' payoffs are supplied.

The training/development blind magnitudes draw from 1, 10, 100, and 1000, while held-out blind test cases use 10000, 100000, and 1000000. These are **sampling scales**, not assertions that all final payoff values fall within those bounds, particularly for large-cancellation variants.

The fixed tie rule is: select the first listed action when its payoff is at least that of the second. The oracle independently reconstructs the source-validated best-response index and rejects any altered prompt, table, target, or metadata. Every target has numerical entries but **blank result cells**, preserving the LoRA prompt-to-math-table objective.

## Acceptance

- Every generated case reconstructs the correct best response and signed payoff residual; a dash indicates an absent calculation, not zero.
- Each blind case has neither a source game title nor its source `game_id` in model-visible text.
- Train, dev, and held-out test contain all nine blind regimes across the 14 verified matrix IDs; cases are generated deterministically from separate seeds and scenario indices, with no duplicated original prompt.
- Report LoRA strict exact reconstruction by blind regime, and guard against game-aggregate results concealing per-regime regressions.
- Run the **separate** `blind-probe` stage after LoRA completion to test held-out anonymous matrices. The probe checks exact rendered table completion. It must not use the oracle math target as a model input.
- `to_numerical` remains original-prompt-only, with optional per-regime numeric error diagnostics.

A successful test supports generalization within the **tested 2×2 full-information best-response problem family**. It does not prove that arbitrary games, mixed Nash equilibrium calculations, scalar objectives, or multi-action sequential games work without their own specified objectives and validation.

## Commands (new output directory required)

Run from the Main Computer repository after installing the flat patch ZIP:

```powershell
& $py -m tools.nanojev_gamer_v1_handoff prepare --output runtime\reports\gamer-v1\blind-001 --blind-matrix-per-game 18
& $py -m tools.nanojev_gamer_v1_handoff lora --output runtime\reports\gamer-v1\blind-001 --device cuda
# After the LoRA run completes, inspect the held-out anonymous matrices:
& $py -m tools.nanojev_gamer_v1_handoff blind-probe --output runtime\reports\gamer-v1\blind-001 --device cuda --allow-experimental
```

The optional `experiment` stage may also prepare and train the new population, but does more than the blind LoRA experiment: it continues through the numerical head. Use the stage-specific commands above for focused validation. The baseline and dev text evaluations remain serial and expensive; this patch does not claim to accelerate them.

If the LoRA process is interrupted, use the existing v8 `--resume` mechanism with the **same run directory, dataset, and hyperparameters**. Do not add blind cases to an existing run or repoint a trained adapter at a new dataset.
