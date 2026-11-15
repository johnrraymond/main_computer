# G2-0047 — Relational-architecture crossover

## Why this test exists

G2-0046 trained shared **readouts** on frozen 48-dimensional post-interaction edge features. Its best model reconstructed 28/111 training graphs but only 3/32 prompt-disjoint transfer graphs. The fixed group/global decoder and count heads can reconstruct the gold graph on the selected TRAIN learn cohort when supplied with oracle edge scores. The question remains: **does the frozen relational representation obstruct learning, does the graph-level objective obstruct learning, or both?**

This experiment does **not** assert that a new decoder, message-passing architecture, or more tests is automatically the answer. G2-0033 already included group pooling and global context. We compare updating those representations with freezing them, under two supervision objectives, while fixing the downstream decision rule.

## Locked 2 × 2 experiment

| Arm | Edge/group representation | Loss |
|---|---|---|
| `frozen:bce` | G2-0033 post-interaction feature **frozen** | Independent edge BCE, G2-0046 nonlinear readout |
| `frozen:structured` | Frozen | Same BCE + exact-decoder loss-augmented graph margin |
| `joint:bce` | A private copy of G2-0033 edge embedding, group embedding/norm, and edge update is **trainable** | Same edge BCE and nonlinear readout |
| `joint:structured` | Same trainable representation | Same BCE + graph margin |

The graph margin directly compares the score of the **actual decoded TRAIN graph** with the score of the TRAIN oracle graph, using the frozen group and global-count terms in the exact decoder's energy. The true graph is used **only for training loss**, never to select inference candidates. For bounded compute, graph-loss decoding is performed on exactly two predetermined examples per eight-prompt batch; the graph-margin term is averaged independently, preventing dilution by minibatch size.

All four arms share: frozen source Qwen/LoRA, frozen G1, the same 111 decoder-reconstructible source TRAIN graphs, 32 separate TRAIN transfer prompts, 80 measurement-only TRAIN fitting prompts, 80 measurement-only TRAIN natural audit prompts, candidate edges, G2-0033 frozen prior, count heads, exact decoder, initialization of the nonlinear readout, 720 training steps, eight prompts per batch, deterministic prompt schedule. For the joint arms only, the internal *diagnostic copy* of the G2-0033 representation is optimized with a separate lower learning rate. The parent checkpoint and its production policy remain unchanged.

The first `frozen:bce` arm must **exactly reproduce** G2-0046's logged exact, missing, extra, and inversion counts across all four cohorts at the reference budget, or G2-0047 refuses to publish any result.

### How to interpret the result

- **Joint BCE beats frozen BCE, at unchanged decoder and supervision:** evidence that learning within the post-G2-0033 relational encoder helps. Next engineering decision: promote a *controlled* upstream representation-training candidate, not another readout.
- **Frozen structured beats frozen BCE:** evidence that using the graph-level training objective matters even without changing features. Next decision: evaluate graph-structured supervision within the proper G2 learning head.
- **Joint structured exceeds all other arms by a substantial margin:** the change in representation and objective interacts; next decision: consider a jointly optimized relational graph learner.
- **All arms fail to transfer substantially:** do **not** advance to more losses or encounter-specialized scoring. Reassess whether the candidate/feature interface exposes enough occurrence, role, direction, and provenance information to support the desired graphs. This is **not** proof that the frozen Qwen/G1 representations lack the signal, because this experiment still uses the G2-0040 feature interface.

The descriptive gate for a *practical* gain is at least **four additional exact graphs** on the 32-case transfer set. This is a predeclared engineering threshold, **not a statistical significance claim** and not a stage certificate. Each report also includes missed/extra edges, rank inversions, candidate-reachable vs unreachable breakdowns, and encounter vs allocation families. Smaller improvements may be interesting but should not trigger another training stage by themselves.

## Run

From `main_computer\\nanojev` with NanoJev's pinned `$py`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-architectural-crossover --device cuda
```

The command refuses if G2-0046's original immutable run is absent, if the parent-hash chain changed, if cohort overlap/drift occurs, or if the output directory already exists. By default the output is:

`runtime/reports/gamer-v1/graph-g2-architectural-crossover-0047/`

Outputs: `g2_architectural_crossover_report.json`, `g2_architectural_crossover_per_case.jsonl.gz`, `g2_architectural_crossover_development.safetensors`, and `g2_architectural_crossover_frozen_policies.json`.

`development_evaluation_skipped=true`, `sealed_test_used=false`, `g2_certified=false`, `production_promotion=false`; the frozen policy is **disabled**. No DEV/TEST data, no learned model selection, and no production mutation.

## Scope limitation

The architecture choice is limited to training **existing post-candidate G2-0033 relational feature construction**, not end-to-end Qwen or G1, not new structural candidate generation, and not a new graph decoder. Failure of every arm narrows the question but does not prove a representational impossibility. Positive transfer here is not sufficient for formal G2 advancement; the original G2 development and challenge gates still apply.
