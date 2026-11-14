# GAMER V37 — Structured Payoff-Role Assignment

## Causal diagnosis

The V36 real-CUDA development run produced 106/120 complete semantic structures, 118/120 correct decisions, and 58/60 correct counterfactual pairs. All 120 probability-referent predictions were correct. The role failure examples frequently exchange two payoff roles. V36 optimized independent role cross-entropies for four number spans, but inference constrained the roles to a permutation of R/S/T/P. The objective was therefore misaligned with the prediction rule.

The V36 DEV diagnostic also found that the frozen V34 numerical decision model reaches 119/120 **with gold DEV semantic inputs**. That one error must not be misdiagnosed as a Qwen role-assignment error. Gold DEV semantics are diagnostic-only and cannot enter training or prediction.

## One experimental change

V37 retains the V36 clause-complete frozen-Qwen features, TRAIN/DEV cases, and the same small role/belief neural head. It **replaces the role training loss only**:

- Previous: sum of four independent per-slot role cross-entropies.
- V37: one cross-entropy over all 4! = 24 complete role permutations.
- Permutation scores are the sums of the same learned per-number role logits used by the pre-existing exact-one-of-each inference decoder.
- Belief C-vs-D training and inference are unchanged.

At inference, the network sees the actual contextual Qwen features, numeric literals, and learned scores. It is not given true payoff roles, an answer label, a hand-coded role parser, or an expected-payoff action oracle. The structural one-of-each constraint has been present since V35 and is not a new shortcut.

## Gates and boundaries

The original V36 numerical scanner accepts four integers and one fractional probability literal. This restriction remains explicit. No claims are made about arbitrary narrative games, self-grounded tactical payoffs, or live tactical authority.

Acceptance retains the original **108/120** complete-semantic, **108/120** decision, and **54/60** pair DEV gates. Only after passing these gates may the V37 run evaluate the separately versioned 200-case sealed holdout. V32 and tactical-v6 remain unchanged. A failed DEV run must leave sealed holdout evidence untouched.

The 200 holdout prompts are generated only in the audit stage. The holdout is not used to select the model, thresholds, epoch, or loss. V37 is an isolated fresh experiment and does not overwrite the V36 run.

Run from repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v37_joint_role_grounding run `
  --run "runtime\reports\gamer-v1\transition-005-v37-joint-role-grounding-0001" `
  --v34-run "runtime\reports\gamer-v1\transition-005-v34-grounded-learning-0001" `
  --device cuda
```

A local test pass shows implementation correctness, not CUDA model convergence. Do not promote V37 unless the real run prints the success status after the holdout audit.
