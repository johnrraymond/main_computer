# GAMER V40 — Actor-ownership semantic grounding

## Why this version exists

V39 identified 13 incorrect V38 payoff-role structures in 120 development cases. Eleven involved confusion between `CD` and `DC` (whose action belongs to whom), and two confused `CC` with `DD`. Qwen's probability-referent interpretation passed all 120 cases. The best V39 linear probe scored 101/120 while V38's interacting head scored 107/120. V39 did **not** prove representation insufficiency.

V40 tests the more specific hypothesis that supervised learning of *actor ownership* will outperform another generic four-label payoff-role classifier.

## Mechanism

1. A numeric scanner transcribes four signed payoff literals and one rational probability without assigning any roles. This is the same scanner as V38.
2. Frozen Qwen base model activations are captured at each numeric token and at each clause endpoint, using the same causal input capture as V38. Cross-clause attention is position-equivariant.
3. Two separately supervised neural heads predict **our C/D** and **the rival's C/D** for each clause. They produce the four role scores (`CC`, `CD`, `DC`, `DD`) by adding the two binary logit contributions. The 24-way complete-role assignment is trained and applied exactly as it is at inference.
4. The unchanged learned probability-referent head and the unchanged, pretrained V34 numerical head produce a decision. V34 receives predicted semantic inputs only.
5. The exact-payoff equation generates TRAIN labels and evaluates development/holdout. It is never used to predict a live answer.

No inference-time regex or lexical dictionary assigns a payoff role or determines who acted. No answer label or source role is passed into Qwen or the neural head during prediction.

## Causal contrasts

V40 changes its TRAIN/DEV generator intentionally; its rows are **not** comparable case-for-case with V38's rows. Each variant contains four different interventions, all with numerical literals held identical within each pair:

- `belief`: the rival's stated 3/4 refers to C instead of D;
- `ours`: toggle which move we make in each outcome;
- `theirs`: toggle the rival's move while ours is held fixed;
- `actor_exchange`: exchange whose move is whose, so CD and DC exchange while CC and DD do not.

Every TRAIN and DEV variant contains all four interventions. Randomized payoff values and shuffled clause order vary independently of the intervention. Forced reversals are precommitted for one-third of the pairs. Digit-only algorithms cannot explain all labels.

V39 also exposed ambiguous prompts: some narratives used `cooperation/defection` or `Yield/Contest` without defining those aliases when the scenario used `share/claim` instead. V40 explicitly defines those synonyms *in the natural-language problem statement*. This is not an inference-time mapping. It makes the target answer semantically well-defined and leaves interpretation to Qwen.

The dataset remains 480 TRAIN, 120 DEV and 200 sealed holdout cases, over the same 15 training-family and five sealed surface-family variants. A fresh dataset seed (40) separates these experiments. Holdout prompts are generated only **after** a passing DEV result; first access creates a one-shot consumption marker.

## Acceptance

DEV (unchanged from V38): at least 108/120 complete role/side interpretations, 108/120 decisions, 54/60 exact counterfactual pairs, and decision accuracy strictly greater than the digit-only ceiling.

Only after DEV passes: sealed test (unchanged): at least 180/200 complete interpretations, 180/200 decisions, 90/100 pairs, and at least 32/40 structural and decision accuracy for each sealed surface family; also outperforms the digit-only ceiling and a constant-action baseline.

### Key limitations

- These are constrained, explicit two-action payoff games, not open-domain game understanding.
- The numeric transcriber imposes a strict four-payoff/one-rational input grammar. The learned head is therefore not an arbitrary text parser.
- V34's numerical head previously missed one DEV example even when provided correct semantics. V40 neither trains nor conceals that error; a DEV diagnostic reports gold-input V34 accuracy separately, without using gold inputs for prediction.
- If V40 passes, the permitted conclusion is improvement on the precommitted semantic counterfactual battery, not proof of tactical competence.
- Space Captain tactical-v6 and V32 numerical authority remain unchanged. No production promotion occurs.
- Local mocked tests validate code paths, not the CUDA learning result.

## Execution

From the repository root, with `$py` already set:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v40_actor_grounding run `
  --run "runtime\reports\gamer-v1\transition-005-v40-actor-grounding-0001" `
  --v34-run "runtime\reports\gamer-v1\transition-005-v34-grounded-learning-0001" `
  --device cuda
```

On DEV failure inspect `dev_failure_diagnostics.json` and `train_result.json` inside the V40 output directory. The holdout remains sealed. The command reuses the original Qwen activation baseline and the accepted V34 checkpoint, but fits a new isolated V40 semantic head.
