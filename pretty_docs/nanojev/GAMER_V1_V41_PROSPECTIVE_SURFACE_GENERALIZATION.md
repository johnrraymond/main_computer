# GAMER V41 — Prospective, surface-disjoint semantic generalization

## Why V40 failed

V40 reached 120/120 on its development partition but only 121/200 exact
semantics and 155/200 decisions on its opened held-out partition. The
reason the development gate gave false confidence is observable in the source:
training and development reused the same table styles 0–4 and belief styles
0–3; the holdout used table styles 5–7 and belief styles 4–7. The two lowest
scoring held-out variants use a payoff-first table style unseen during fitting.

V40 is **rejected** as a generalization certificate. Its holdout has been
opened and must not be re-used as a prospective blind test. There is no
credible basis to claim a training-run improvement from this change alone.

## V41 controlled experiment

V41 keeps the frozen-Qwen clause capture, actor-ownership attention head,
TRAIN-fitted V34 numerical head, same counterfactual label oracle for
*dataset construction only*, and one-shot holdout policy.

It changes the **experimental design**, not the learned policy by fiat:

| Scope | Table language families | Probability language families | Examples |
|---|---|---|---:|
| TRAIN | 0–7 | 0–7 | 480 |
| DEV | 8–10 | 8–10 | 120 |
| Prospective TEST | 11–15 | 11–15 | 200 |

The already-exposed V40 formats 5–7 are eligible for new TRAIN examples.
No V40 test **examples, cached features, labels, or checkpoint predictions**
are ingested, and the previously exposed variants are not reused for scoring.
V41's new TEST prompts are only constructed if the DEV gate succeeds.
These are *precommitted software gates*, not cryptographic secrecy: the
template definitions are visible in source code.

Every pair retains identical five numerical literals but changes the
actor/action meaning or the probability referent. TRAIN and DEV use new
independent seeded numbers. DEV contains entirely unseen payoff and belief
expression families, including a number-before-role format.

## Inference contract

Qwen hidden states and the learned actor/belief head assign meaning. The
bounded numeric scanner transcribes four integer literals and one fraction;
it neither assigns CC/CD/DC/DD nor chooses C/D. V34 makes the action choice
from the reconstructed five numerical features. The supervised oracle is
never invoked to generate a prediction. V32 authority and tactical-v6 are
never changed.

## Acceptance

DEV must have at least 108/120 exact semantic structures, 108/120 final
decisions, 108/120 correct belief referents, and 54/60 complete pairs.
Development accuracy must exceed the digit-only ceiling. Otherwise V41 stops
without constructing a TEST example.

Only after DEV passes can prospective TEST open once. TEST must reach
180/200 exact semantic structures, 180/200 decisions, 180/200 belief referents,
and 90/100 complete pairs, with every test family clearing 32/40 semantics
and 32/40 decisions. Saved TEST predictions and features can be replayed
from the checkpoint using the `verify` command. This is **not** acceptance for
arbitrary game mechanics, tactical action selection, or deployment.

## Usage

In Windows PowerShell, from the Main Computer repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v41_surface_generalization run `
  --run "runtime\reports\gamer-v1\transition-005-v41-prospective-surface-0001" `
  --v34-run "runtime\reports\gamer-v1\transition-005-v34-grounded-learning-0001" `
  --device cuda
```

For an independent saved-checkpoint replay after a completed test:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v41_surface_generalization verify `
  --run "runtime\reports\gamer-v1\transition-005-v41-prospective-surface-0001"
```

The trainer deliberately reuses the V40 head/optimizer, so epoch progress
retains a `GAMER_V40_EPOCH` prefix. V41's own `GAMER_V41_TRAIN`,
`GAMER_V41_HOLDOUT`, and `GAMER_V41_REPLAY` records identify this experiment.

## Limitations

- Success or failure is unknown until the Windows CUDA run.
- The formats are synthetic and explicitly bounded; even passing an unseen
  style split would not prove arbitrary natural-language semantic reasoning.
- The opened V40 holdout is **historical** diagnostic evidence, never again
  a valid blind test.
