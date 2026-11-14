# GAMER V1 — Radius Recovery Microscope

This is a **read-only diagnostic**, not a new C0 training scheme. It starts from
an existing expanded-head-100 checkpoint and uses synthetic numeric substitutions
in source-certified prompt contexts. The substitutions themselves are not
certified game states or game-answer targets.

## One-step larger experiment

Run from the repository root with the usual Python launcher variable `$py`:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head-100-radius `
  --run runtime\reports\gamer-v1\transition-005-expanded-head-100-0001 `
  --radius-contexts 48 `
  --radius-width 4 `
  --radius-recovery `
  --device cuda
```

This captures 48 independent games at the 9 literal values `-4..+4`, using
frozen Qwen LoRA and the expanded-head best checkpoint, then analyzes the saved
activations. The exact number of eligible contexts is reported; any context
with a token-count mismatch is excluded from the decoder comparison rather
than being silently treated as equivalent.

The command writes a new `*-radius-activations-NNNN` folder, then a new
`*-radius-activations-NNNN-recovery-NNNN` sibling. Neither is written over the
checkpoint or certified dataset. Source fingerprints are checked.

## Reanalyze existing captures without CUDA

The original 12-context, radius-2 capture can be processed without recapturing:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head-100-radius-recovery `
  --run runtime\reports\gamer-v1\transition-005-expanded-head-100-0001-radius-activations-0001
```

Alternatively:

```powershell
& $py -m tools.nanojev_gamer_v1_radius_recovery `
  --radius-run runtime\reports\gamer-v1\transition-005-expanded-head-100-0001-radius-activations-0001
```

## Questions measured

1. **Within the same game context:** a decoder trained on all numerical radii
   *except one* predicts that held-out numerical value. Linear projection and
   nonlinear RBF are both tested. The zero-intervention activation is supplied
   as the reference, so this is *not* standalone number extraction.
2. **Across unfamiliar games:** training games determine the numerical
   slope/curvature subspace, its scale, and both linear and anchored-RBF
   decoders. Whole games, not individual values, are held out. The held-out
   game's zero intervention is still required to center the vectors. The
   comparisons measure transferable numeric *changes*, not absolute numbers
   from prompts.
3. **At zero:** symmetric radius-1 (and all symmetric-radius) activations are
   tested for signed opposition. For a perfectly linear signed coordinate,
   zero-midpoint error is `0` and negative/positive displacement cosine is
   `-1`; a sign-blind, absolute-value-like response has error near `1` and
   cosine near `+1`. These **do not** score exact zero game answers.
4. **Controls:** unchanged suffix embeddings and shuffled training labels
   provide negative controls. The shuffled labels preserve the training-game
   projection basis; they test the decoder-label relationship, not the basis.

## Artifacts

The recovery folder contains `final_report.json` and
`recovery_predictions.npz`. The report includes per-stage within-context and
held-out-game R², numerical RMSE, sign retention, zero-midpoint ratios,
per-context results, and a compact `summary.layer_comparison` that tracks the
Qwen-final-100 -> C0-input -> C0-hidden-1 transition. Predictions retain the
eligible-context indices so excluded tokenizer-mismatch contexts can be audited.

## Interpretation discipline

- **Within-context succeeds, cross-game fails:** a context-specific numerical
  code is plausible; this does not prove information has been destroyed.
- **Cross-game nonlinear beats linear:** recoverability is sensitive to the
  decoder family, consistent with nonlinear or nonlinear-coordinate coding.
- **Both decoder families fail:** the probes did not recover the number; that
  is not an information-theoretic proof that it is absent.
- **Zero-midpoint near 1, displacement cosine near +1:** the activation
  geometry treats signed opposite magnitudes similarly at that layer.
- **Embedding-stage success alone is not arithmetic:** Qwen could be
  responding to signed-digit token identity rather than numerical structure.

No LoRA or C0 weight updates occur. Results from 48 contexts are stronger than
12 but still a bounded probe, not an architecture certification.
