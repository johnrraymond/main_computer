# XYOP mask-matched curriculum advancement — V2

## Defect corrected

The original XYOP runner used the configured `--xyop-mix` for training but forced
`000` (all information hidden) for every development advancement probe. That
measured a different learning task. In particular, training with `011` (X hidden;
Y and OP visible) was evaluated without Y and OP.

## Advancement contract

- **Training and advancement use the same mixture**, resolved independently for
  each tier and scientific sublevel using the existing precedence rules.
- A held-out development row supplies **only the values whose visibility bits
  are 1**. For `011`, the information channel contains `X=(0,0)`,
  `Y=(1, true_y / 1_000_000)`, `OP=(1, MAX)`. Development labels are only used
  as **explicitly visible input hints** or for post-prediction scoring, never
  substituted for model predictions.
- Advancement scores **only the masked (hidden) numerical outputs**. For `011`,
  the joint gate is X accuracy only; an incorrect prediction of visible Y does
  not fail that gate. For `000`, both X and Y must be correct. Training loss
  remains the existing V3 X/Y loss.
- For mixture clauses such as `T02:3=011:3,000:1`, each held-out row is evaluated
  under both patterns; the score is the exact weighted average (75%/25%), not
  an RNG-dependent sample. Thus training and advancement share the configured
  per-sublevel distribution.
- `110` and `111` have no masked X/Y output to score. They may assist training,
  but do **not** automatically count as passed evaluation cases. They are
  excluded from the conditional denominator and their excluded weight is
  reported. If a tier has no scoreable mask, advancement fails explicitly.
- **V3 is the sole predictor**. The old numerical sidecar remains inert, does
  not receive a loss, and cannot cause a gate to pass.
- The sealed test split is not accessed. Other diagnostic paths that do not
  select a masked development evaluation remain all-hidden.

## Installation

Stop any active NanoJev trainer, then apply this minimal patch using the
existing Main Computer patch tool:

```text
python new_patch.py nanojev_hawk_dove_xyop_mask_matched_advancement_v2_patch.zip --dry-run
python new_patch.py nanojev_hawk_dove_xyop_mask_matched_advancement_v2_patch.zip
python -m unittest tests.test_nanojev_hawk_dove_xyop_mask_train -v
```

**Do not run the XYOP upgrade again.** This patch changes the scoring runtime,
not the decoder architecture or saved weights. The existing V1 checkpoint
contract is accepted and its evaluation-policy field is updated automatically
upon the next ordinary training checkpoint save.

## X-hidden, Y/OP-visible, 10% gates

Read-only gate check with the active NanoJev Python interpreter:

```text
python -m tools.nanojev_hawk_dove_xyop_mask_train --xyop-mix "*:*=011:1" probe --device cuda --gate-percent 10
```

Then continue training, if desired:

```text
python -m tools.nanojev_hawk_dove_xyop_mask_train --xyop-mix "*:*=011:1" --additional-steps 3000 train --device cuda --chunk-steps 300 --gate-percent 10
```

Use the same `--xyop-mix` flags when running **standalone** probes, because
those commands have their own CLI invocation. A training run's internal
advancement probes automatically use that training invocation's mixture.

Look for `xyop_v3_only_runtime` with
`eval_visibility=MATCH_TRAIN_XYOP_MIX`, `advancement_scoring=HIDDEN_XY_ONLY`.
The saved per-tier result includes `assessment=MATCH_TRAIN_XYOP_MIX`,
`objective=masked_hidden_xy_v3`, and `visibility_weights`. The field
`joint_5pct` is now **conditional hidden-target accuracy**, not the old
unassisted two-output score. To measure independent reconstruction, run with
`--xyop-mix "*:*=000:1"` explicitly.

## Validation

CPU unit tests cover the gated 011 case (including intentionally wrong visible
Y), a weighted mixed-mask case, refusal of unscoreable mixtures, exact
forward-path mask injection, sidecar isolation, legacy-checkpoint compatibility,
and the checkpoint-save policy. CUDA/Qwen integration still needs to run on the
user's machine.
