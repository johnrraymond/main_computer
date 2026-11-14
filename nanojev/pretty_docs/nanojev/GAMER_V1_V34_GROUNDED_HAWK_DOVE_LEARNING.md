# GAMER V34 — grounded game learning after V33's failed DEV gate

## Failure and causal scope

V33's Qwen-vector-to-two-action head reached only **47/90** exact on development, against a required 72/90. Its five held-out variants were not scored and no tactical authority was changed. It is possible that the pooled activations discarded relevant numerical structure, or that the particular classifier/optimization could not recover it; V33 alone does not distinguish those hypotheses.

A simple parsed-payoff neural head cleared **90/90** of those original development examples, but then scored only **69/118** on an independent asymmetric-payoff diagnostic. This exposed a flaw in the original benchmark's causal identifiability: V33 made both payoff contrasts equal and opposite, so the correct choice can be inferred just from the sign of one contrast and the side of 1/2 on which the opponent probability falls. Such a model does not need to learn the true expected-payoff comparison.

V34 therefore includes independently varying payoff contrasts and both **reversal** and **non-reversal** probability pairs. On these cases, that shortcut scores **222/300** on TRAIN-family development; the V34 asymmetric development gate requires **270/300**.

## What V34 learns, and what it does not

V34 reads five raw, input-supplied quantities from the prompt: four payoffs `(R,S,T,P)` and the rival's cooperation probability `q`. It passes five normalized raw inputs into a trainable neural action head. It **does not** compute expected action payoffs, their difference, its sign, or the correct response at inference. The precommitted game-payoff teacher is used only to generate TRAIN labels and for DEV/holdout scoring **after** predictions. V34 never uses ground-truth targets as features.

Unlike V33, **V34 does not infer the raw numerical quantities from Qwen hidden states**. Its bounded text parser recognizes only the four explicit table layouts in the Hawk/Dove deck and fails closed for unknown or ambiguous formats. This is an experiment in **learned decision-making from grounded game-state inputs**, not evidence that Qwen has learned to extract or reason over arbitrary free-form games. No production Qwen, LoRA, V32 numerical authority, or tactical-v6 checkpoint is modified. The small supervised head is trained deterministically on CPU even when the command includes `--device cuda` (retained for the existing Captain service interface).

## Training / evaluation split

There are 20 whole prompt surface variants, unchanged from V33: 15 training-family variants and five sealed surface variants (IDs `1, 6, 11, 16, 19`). Each training-family surface has 12 original symmetric game pairs (9 TRAIN + 3 DEV), plus 40 new asymmetric pairs (30 TRAIN + 10 DEV). Each held-out surface has 12 original symmetric pairs and 20 new asymmetric pairs.

| Split | Original symmetric | New asymmetric | Total |
|---|---:|---:|---:|
| TRAIN | 270 | 900 | 1,170 |
| DEV | 90 | 300 | 390 |
| Sealed TEST | 120 | 200 | 320 |

Each pair receives opponent cooperation beliefs `q=1/4` and `q=3/4`. In the asymmetric set, one third of pairs require the correct decision to **reverse** when the belief changes, another third require the decision to **remain unchanged**, and the remaining pairs vary all payoffs independently. This prevents a simple belief-side or constant-choice policy from qualifying as learned game optimization. It still does not measure generalization to unseen game mechanics or probability distributions.

## Acceptance gates

DEV is checked before any actual held-out scoring:

* Original examples: at least **72/90** exact and **36/45** completely correct pairs.
* Asymmetric examples: at least **270/300** exact and **135/150** completely correct pairs.

If either DEV gate fails, all five TEST surfaces remain unopened within that run. After passing DEV, the holdout is consumed **once**, with an irrevocable sentinel created before constructing or scoring any test examples. The audit requires:

* Original symmetric TEST at least **96/120** exact and **48/60** complete pairs.
* Independent asymmetric TEST at least **180/200** exact and **90/100** complete pairs.
* At least **51/64** correct on each of the five unseen prompt surface variants, and better than a constant-choice baseline.

The saved predictions are tied to the checkpoint, training manifest, and regenerated prompt hashes. A changed or incomplete ledger is rejected. Passing this suite authorizes only **shadow inference** through `TacticalAIService.shadow_gamer_hawk_dove` with the v34 request schema. Contest maps to `approach`, Yield maps to `withdraw`; both are proposals only. It cannot dispatch a helm order, initiate boarding, or replace tactical-v6 authority.

## Run

Extract the patch into the Main Computer repository root, preserving paths. Keep the failed V33 run untouched. Use a new report directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v34_grounded_game_learning run `
  --run "runtime\reports\gamer-v1\transition-005-v34-grounded-learning-0001" `
  --device cpu
```

A complete pass prints `GAMER_V34_PASS_V34_GROUNDED_GAME_LEARNING_SHADOW_ACCEPTANCE`. In local source/optimizer tests with a mocked V32 authority receipt, the revised V34 head reached **389/390** on TRAIN-family DEV: 90/90 original and 299/300 asymmetric, including 149/150 asymmetric pairs. These are **not** live-host or independently scored sealed-test claims.

Output files include `train_manifest.json`, `train_history.json`, `grounded_head.pt`, and `train_result.json`. If DEV passes, the one-time holdout creates `holdout_consumed.json`, `holdout_predictions.json`, and `holdout_acceptance.json`. If holdout passes, Captain shadow acceptance creates `captain_shadow_smoke.json` and `operational_acceptance.json`.

## Further proof needed

This V34 bridge cannot derive payoff tables or rival behavior from Space Captain sensors; it does not generalize to a different game's rules, nor choose a full tactical-v6 legal action set. A passing result establishes supervised decision acquisition from explicitly supplied payoffs, with controlled withheld surface variants. The next substantive challenge is connecting a perception/numerical decoder to the learned choice head and evaluating full battle trajectories against a baseline under independent game-state variation.
