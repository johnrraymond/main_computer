# GAMER V33 — learned T08 decision head, sealed 15/5 holdout, Captain shadow

## What V33 actually tests

V32 promoted V29/V30 **only** for pre-certified signed-difference prompts. Its neural readouts locate and decode numbers; the final subtraction is exact deterministic postprocessing. Numerical correctness does **not** establish that a model learns to choose actions in a game.

V33 adds a separately trained **two-action neural preference head** for 20 T08-style Hawk/Dove narrative prompt surfaces. The target is the best expected-payoff action, **Contest (D)** or **Yield (C)**, conditional on a stated opponent action probability. This tests a learned narrative-to-decision mapping against a novel-surface holdout. It is a **single game family**, not arbitrary games.

The prompt contains the captain's action labels, payoff narrative, and opponent belief. It does **not** contain its target label, oracle-computed expected payoffs, margin, mathematical ladder, or a derived sign. At inference, the readout receives only three frozen base-Qwen hidden-state summaries (layer 9 final token, layer 18 final token, and layer 18 sequence mean). The V33 MLP is trained by cross-entropy on labeled TRAIN examples. Neither V29/V30's subtraction nor any payoff parser computes the prediction. The oracle is used only to assign training labels and **after** predictions to evaluate accuracy.

## Independent-split protocol

The 20 predeclared whole prompt variants are the cross-product of five different encounter descriptions/question wordings and four table renderings. Fifteen variants are used for training and development. Five variants — `1, 6, 11, 16, 19` — are withheld completely until a one-time sealed audit. Across every variant are 12 **paired** payoff games. Each pair is identical except the rival cooperation probability changes from `1/4` to `3/4`, which reverses the optimal action. A constant-choice policy gets at most one of each pair correct.

* TRAIN: 15 variants × 9 pairs × 2 belief levels = **270** prompts.
* DEV: same 15 variants, disjoint payoff pairs, 15 × 3 × 2 = **90** prompts.
* TEST: five unopened surface variants, 5 × 12 × 2 = **120** prompts.
* No overlapping prompts or payoff-pair IDs among these sets.
* The frozen base model never receives oracle labels as inference features; model weights and the V32 selector/decoder are unchanged.

Training may choose the best head epoch on DEV only. **DEV must score at least 72/90 before TEST is opened.** The audit writes `holdout_consumed.json` before generating/test-forwarding a single held-out example, and refuses a second audit in that run even if the first fails or crashes. The required TEST conditions are:

1. Exact decisions at least **96/120**.
2. Both halves correct in at least **48/60** belief-reversal pairs.
3. At least **16/24** on every individual held-out surface.
4. Better than the constant-majority baseline (60/120).
5. Full V32 source and authority fingerprints still valid.

A passing TEST enables **shadow inference only**. This is intentionally not an automatic promotion to tactical-v6.

## Space Captain boundary

`TacticalAIService.shadow_gamer_hawk_dove` accepts a typed `game.bridgeGamerShadowRequest.v33` with a `game.bridgeCaptainObservation.v1`, an external T08 payoff narrative, and the V33 report directory. It validates captain beta/ship beta, its mission, range/position consistency, finite telemetry, and the boarding lifecycle. Only states where both **approach** and **withdraw** are legal are supported.

The learned choice `Contest` maps to a non-authoritative `approach` proposal; `Yield` maps to a non-authoritative `withdraw` proposal. Any deployed/committed boarders cause refusal **before** invoking the model. The bridge returns `captainOrder: null`, `actionPublished: false`, and does not alter the existing combat or helm dispatch. It **does not** infer payoff values from sensors. The CLI runs a fresh, independently scored scenario through the actual `TacticalAIService` method after the sealed holdout passes.

## One-command CUDA protocol

From the Main Computer repository, with V32 already active:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v33_game_learning run `
  --run "runtime\reports\gamer-v1\transition-005-v33-game-learning-0001" `
  --device cuda
```

The command trains the head, checks DEV without TEST, performs the one-time sealed TEST audit only if DEV passes, and only if TEST passes invokes the real Captain shadow interface. A complete success reports `PASS_V33_GAME_LEARNING_SHADOW_ACCEPTANCE`, **not** a tactical-v6 promotion. If DEV fails, TEST remains unopened; if TEST fails, it is consumed and no Captain smoke runs. Do not tune to the five consumed TEST variants.

To inspect the report without re-scoring TEST:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v33_game_learning status `
  --run "runtime\reports\gamer-v1\transition-005-v33-game-learning-0001"
```

Outputs: `train_manifest.json`, `train_history.json`, `learned_head.pt`, `train_result.json`; if audited, `holdout_consumed.json`, `holdout_predictions.json`, and `holdout_acceptance.json`; if acceptance succeeds, `captain_shadow_smoke.json` and `operational_acceptance.json`.

## What a passing experiment would — and would not — prove

A real CUDA PASS would establish that a learned neural head classifies held-out prompt **surface variants** and conditional Hawk/Dove action choices, and can present its decision as a legal shadow Space Captain intent. It would not establish transfer to previously unseen **game mechanics**, learning a payoff model from live sensor observations, adaptive online learning, tactical battle success, the 5-second battle latency budget, or arbitrary-game generality. Those require separate independent experiments and battle-trajectory evidence. No automatic cutover is permitted from this result.

The saved holdout predictions are bound to their prompt hashes and to the acceptance report. Shadow inference verifies the prediction ledger against a regenerated, independently scored sealed deck; a changed checkpoint, changed source, altered prediction file, or invalid acceptance status fails closed. These checks concern provenance and audit integrity; they never substitute ground-truth labels for neural predictions.
