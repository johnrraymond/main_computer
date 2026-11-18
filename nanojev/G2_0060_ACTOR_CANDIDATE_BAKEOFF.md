# G2-0060 — frozen-G1 actor candidate bake-off

## Objective and factual starting point

The G2-0059 unseen canary captured 128 new counterfactual **pairs** and reached only **116/128 (90.625%)**, below the 98% coverage gate. Its saved sidecars identify **46 missing gold `owns` edges**, all originating in **16 excluded actor occurrences** across 12 pairs; 29 missed edges trace to a positive-margin raw G1 actor proposal and 17 to a negative-margin one. The four family/mode cells and distractor strata were not uniformly successful. The prior 16/16 pilot did not generalize sufficiently.

This patch does **not** declare a new heuristic victorious. It implements a controlled, source-frozen comparison of four actor candidate policies under the existing G2-0052 **192-anchor ceiling**. The objective is candidate representability and *absence of newly lost gold edges*, not graph-prediction accuracy.

## Four predeclared arms

- `evidence`: unchanged G2-0059 inference-time evidence policy. This is the required control and its replay must reproduce the saved 128-pair canary **edge identities**, not merely the aggregate 116/128 number.
- `actor_quota`: preserve the frozen G2-0051 anchors and high-priority numeric/repeated evidence; reserve up to 20 supplemental places for distinct G1-scored actor spans (selection remains neural; G1 weights are frozen).
- `actor_cues`: place prompt-derived participant actor spans ahead of low-priority spatial leftovers; uses only visible English syntax and frozen tokenizer offsets, not graph annotations or name dictionaries.
- `hybrid`: the prompt-cue rescue plus G1 actor reservation, within the same budget.

For all four arms, **one** frozen G1 capture supplies the same raw logits and tokenizer alignment per prompt. Each arm constructs its candidate inventory independently; gold edges are accessed only after all four inventories are fixed. The 0051 admitted anchors and their typed edges are preserved; all proposed actors compete for the **same 192-anchor maximum**. The report measures number of retained actor anchors, typed candidate edges, gold-edge misses, gold-edge regressions, recoveries, and both-graph reachable pairs by family/mode and distractor class.

The syntactic-cue arm is deliberately a hypothesis, not a general-language claim: its patterns are recognizably similar to the current synthetic corpus author. A pass on a fresh sample from the *same author* is stronger than fitting the failed canary but still does not prove transfer to unseen language or application domains. A later independent author/grammar test is required before general claims of generalization.

## Stage 1 — development bake-off (known failure cohort)

Apply the ZIP at the `main_computer` repository root. From the `nanojev` directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale actor-bakeoff --stage development --device cuda
```

This replays the **128 canary pairs at training indices 16–143**, one frozen capture per batch shared across four arms, and checks the `evidence` arm against the already committed G2-0059 rollout metadata. It saves 16-pair audit shards at:

`../runtime/reports/gamer-v1/graph-g2-0060-actor-bakeoff/development-*.json`

An interrupted session resumes by verifying each saved audit shard's SHA256, pair IDs, prompts, source hashes and plan configuration. `--max-shards 1` is available to bound one session. Resume by rerunning the same command without changing files. The output `development-selection.json` is created only when all 128 pairs are complete. The selection rule is **predeclared**: zero introduced missing edges first, then passing coverage gates, then fully reachable pairs, fewer missing gold edges, smaller candidate inventory, fixed tie order. No tuning on the holdout.

## Stage 2 — independent holdout

Only after the development selection is committed:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale actor-bakeoff --stage holdout --device cuda
```

This captures **128 previously unexamined training pairs at indices 144–271**, disjoint from both the original 16-pair pilot and the prior 128-pair canary. The selected arm is *locked before holdout*. Other arms are reported for scientific comparison but **cannot replace the locked winner** after their holdout results become visible. `holdout-result.json` reports the pass/fail gate. The prior failed canary shards and all previously captured feature tensors remain immutable; no test, challenge, or validation split is accessed.

## Acceptance and controls

The locked winner must reach at least **98% both-graph pair coverage**, at least **95%** in every sufficiently sampled family/mode and distractor stratum, adequate topology diversity, and **zero newly missing gold edges** relative to the unchanged evidence control on holdout. Any failure retains `production_promotion=false` and prevents continuation of the previous full rollout. Candidate budget and source-neural frozen status are checked per sample; gold annotations do not steer selection.

No G1 or G2 weights are trained, modified or promoted in this bake-off. The command does not silently change the G2-0059 `rollout` default policy. If the selected arm passes the independent holdout, a **separate, explicitly reviewed** integration is needed to make that arm available to full-scale feature capture. Even a holdout pass would establish coverage on the current generator, not independently certify model accuracy.

## Local verification scope

CPU tests check all eight supported author prompt constructions, lexical-cue bounds and token eligibility, fixed-anchor and 192-budget preservation, regression penalties, four-cell stratification, SHA256 tampering refusal, staged capture/restart, frozen development selection, and the prevention of post-hoc winner changes after holdout. These are synthetic capture tests, **not the real CUDA run**; all real numerical results require executing the two commands on the operator's frozen model stack.

## Measured CUDA result and handoff to G2-0061

The executed development comparison gave **121/128** reachable pairs for both `actor_cues` and `hybrid`, vs **116/128** for evidence and **113/128** for actor quota. The independently held-out results were: evidence **119/128** (26 missing `owns` edges), actor quota **114/128** (49 missing, 28 regressions), contextual actors **125/128** (11 missing, zero regressions), hybrid **125/128** (11 missing, zero regressions). Development locked `hybrid` by its predeclared tie-breaker, but its holdout failed the 98% overall gate and the encounter-renaming cell. Do not present G2-0060 as a certification success.

User decision: further candidate-edge repair is deferred while G2 learning is measured. G2-0061 takes contextual-only candidate recovery into an **explicit research experiment**, not a production promotion. Gold-incomplete pairs are excluded from supervised training; missing edges remain visible in a separate reachability and full-validation report. The 98% formal gate remains unchanged. See `G2_0061_EXPERIMENTAL_GRAPH_LEARNING.md` for execution, quality checks, and limitations.
