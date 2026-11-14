# GAMER V35: Qwen contextual role and probability grounding

## Objective

V34's 319/320 holdout concerns a **five-number neural decision task**, not language comprehension. The regex parser assigned `CC`, `CD`, `DC`, `DD` and incorrectly treated any fraction as `P(opponent chooses C)`. V35 removes that semantic parser from the inference route and tests whether *frozen Qwen contextual representations* support a learned grounding head.

## Authority boundaries

V32 numeric authority unchanged. V34 numeric choice network loaded from its existing audited checkpoint, never retrained. V33 remains a failed pooled-language baseline. No new Captain or tactical-v6 authority. A V35 PASS is only a scoped two-action language-grounding shadow acceptance.

## Causal path

Raw prompt -> **literal-number transcription only** (four signed integers and one proper rational, no role meaning) -> frozen base Qwen layers 9/18 contextual activations at each numeral and nearby future tokens -> learned, separately trained role head (one of `R,S,T,P` for each integer) and probability-referent head (`P(C)` vs `P(D)`) -> Qwen-predicted structured values -> frozen, trained V34 decision head -> action.

There is no role/meaning regex, no semantic assignment from word order, no exact_teacher call, no arithmetic oracle, no ground-truth label, and no V34 `ground()` call on the live prediction path. The loader checks accepted V34 model/report fingerprints without regenerating its teacher-labelled test deck.

A digit scanner is still used for transcription; this does **not** establish arbitrary free-text parsing or numeric transcription beyond four integer payoffs and one fractional probability. The model remains bounded to two-action payoff comparisons. If the fast tokenizer cannot align numeric spans, V35 fails closed.

## Discrimination and leakage prevention

The dataset contains numerically identical minimal pairs. Half change whether an unchanged fraction describes rival C or rival D. Half change the association between the same four numeric tokens and payoff outcomes. Text and numbers retain identical numeric order inside each pair. Every third pair reverses the correct decision by construction, and other pairs include asymmetric comparisons. Randomized payoff ordering prevents a fixed-position role map from succeeding. Narrative themes vary independently; the five withheld surface families use unseen payoff and belief sentence forms.

TRAIN/DEV: 480 / 120 examples, using 15 surface families with entire payoff pairs held out of fitting. Sealed TEST: 200 examples across five withheld surface families, consumed at most once after a genuine DEV pass.

Development gate: >=108/120 exact structural assignments (all four roles AND probability referent), >=108/120 exact downstream decisions, >=54/60 complete counterfactual pairs, and decision accuracy above the numeric-only ceiling for its actual reversal count.

Sealed-test gate: >=180/200 exact structural assignments, >=180/200 decisions, >=90/100 complete counterfactual pairs, >=32/40 semantic assignments and decisions in every withheld family, plus accuracy above the mathematical ceiling of any predictor that produces the same answer for numerically identical counterfactual pairs.

DEV selection is allowed only for the train-family examples. A failed DEV must **not** create/open the holdout. Holdout consumption is recorded before test deck generation or scoring. Checkpoint and numeric model fingerprints are verified. The sealed report stores the Qwen feature arrays and predictions, and subsequent audited loads recompute predictions from the saved head and features to detect falsified result ledgers.

## Run (PowerShell, repository root)

```powershell
& $py -u -m tools.nanojev_gamer_v1_v35_semantic_grounding run `
  --run "runtime\reports\gamer-v1\transition-005-v35-semantic-grounding-0001" `
  --v34-run "runtime\reports\gamer-v1\transition-005-v34-grounded-learning-0001" `
  --device cuda
```

The run uses frozen base Qwen on CUDA for feature capture, trains a small contextual semantic head on CPU, selects using DEV, and opens the sealed holdout **only if DEV passes**. All source weights remain unchanged. One-shot test acceptance is not permission to replace tactical-v6.

## Interpreting results

- `FAIL_DEV_HOLDOUT_STILL_SEALED`: Qwen+probe has **not** recovered the semantic distinction. Do not claim success or silently fall back to V34's regex parser.
- `FAIL_V35_SEMANTIC_SHADOW_ONLY`: DEV passed; withheld semantics failed. No retraining against the opened holdout. Start an independently specified training revision and a new sealed evaluation family.
- `PASS_V35_SEMANTIC_SHADOW_ONLY`: scoped learned semantic grounding demonstrated on the precommitted corpus; still no arbitrary new game ontology, telemetry-derived payoffs, or tactical authority.

For additional real-prompt audit after PASS, run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v35_semantic_grounding infer `
  --run "runtime\reports\gamer-v1\transition-005-v35-semantic-grounding-0001" `
  --device cuda `
  --prompt "A team chooses C or D. CC=0; CD=0; DC=10; DD=-10. The rival chooses D with probability 3/4; otherwise C. Which action is better?"
```

This held-out audit and new-prompt inference must be run on the user's Windows CUDA host. Offline tests certify code contracts and failure behavior, not Qwen semantic accuracy.
