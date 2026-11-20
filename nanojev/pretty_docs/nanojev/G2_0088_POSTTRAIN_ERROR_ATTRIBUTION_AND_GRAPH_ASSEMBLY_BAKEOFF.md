# G2-0088 — Post-Training Error Attribution, Graph Assembly Bakeoff, and Fuzz

G2-0088 is a **frozen G2-0087 experiment**. It does not train, write a new head checkpoint, edit G1/G2 certified source, promote a decoder, or unlock G3.

## Run

From `main_computer\nanojev`, after extracting the ZIP at the `main_computer` root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 run --device cuda
```

If interrupted, inspect which phase completed. Do not rerun `run` against an existing output; resume using the corresponding phase:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 predict --device cuda
& $py -u -m tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 score
```

The default immutable destination is `../runtime/reports/gamer-v1/oracle-fabric-g2-posttrain-graph-bakeoff-0088`.

## Source of evidence and experiment design

G2-0087 is the unchanged parent. The program validates the parent head, G1 native certificate lineage, 0087 seal and prediction hashes, and independently recomputes prior development and challenge exact reconstruction and edge F1 before using those data.

The previously scored 0087 **96 development cases** are *decoder calibration*, and its **64 challenge cases** are *decoder-family selection*. Both were exposed in prior research and are **not** independent certification. Selection and parameters are locked before creating or predicting the fresh cohort. The final scorer does not refit any parameter.

The frozen bakeoff compares the actual native decoder, a global raw edge-versus-null margin, source-conditioned top-k candidates, source/destination/binding-type threshold calibration, and a deterministic **greedy** joint edge assembler enforcing learned outgoing and incoming capacities. The joint assembler is *not* claimed to find the global integer optimum or to satisfy undocumented graph constraints. All policies use identical raw G2-0087 scores; no gold data enter deployed inference.

The diagnostic battery reports false positives, missed reachable edges, G1-unreachable edges, and wrong binding labels by type. It also reports **oracle-only** top-k candidate coverage at budgets 1, 2, 3, 5, 10 and unlimited. Oracle top-k uses gold to form comparison groups only **after** inference, and therefore cannot be used as a deployable graph decoder or certification accuracy estimate.

Once the old-selection winner is frozen, the runner prepares **160 fresh author-generated cases** (96 development and 64 challenge), excluded from the earlier curriculum and G1/G2 experimental prompt commitments; additionally it includes the previously exposed historical development/challenge cohorts (80/32) and **eight author-annotated counterfactual pairs** (16 inputs). Modes cover renaming, role swap, irrelevant distractor, and action removal. A seal commits answers and prompt digests before prediction; the prediction subprocess verifies the seal without opening the new answers and records the raw logits/margins along with all inference arms. A separate process verifies the locked prediction hash and recomputes selected decoder outputs before opening the committed gold answers.

The scorer compares selected decoder, native G2-0087 decoder, G2-0083 head, untrained head, zero-hidden causal ablation, and no-binding causal ablation. It reports graph exact, edge precision/recall/F1, G1 coverage ceilings, and fuzz invariance after author-ID normalization. It checks the original G2 exact-graph gates on both historical and fresh cohorts (development >= 0.80, challenge >= 0.70), the neural and stage component causal gains (>=0.08) and paired superiority over control, with no gate weakening. The output is explicitly **not** a native G2 certificate: `g2_certified: false`, `g3_training_unlocked: false` even if every numeric gate passes.

## Outputs

- `diagnostics.json`: exposed-cohort error attribution and oracle candidate ceilings
- `selection.json`: frozen decoder-family comparison and chosen policy
- `seal/commit.json`, `seal/prompts.jsonl`, `seal/answers.SECRET.jsonl`: immutable commitments and gold
- `blind/predictions.jsonl`, `blind/lock.json`: frozen blind results, raw margins, all arms
- `scored/report.json`, `scored/cases.jsonl`: historical/prospective scores, causal ablations, G1 reachability, fuzz checks, blockers

**Read the first completed phase before resuming.** Existing immutable output files are never silently overwritten. A failed `predict` before writing its `blind` directory can be rerun. An already-written `blind` directory is intentionally immutable. Scoring cannot run twice in the same destination.

## Interpretation and limitations

- Fresh author-generator cases are distinct prompts but **not** an out-of-distribution test.
- Greedy graph assembly does not prove that a full learned joint decoder would work.
- Calibration and selection fit already-exposed gold; the final held-out phase, not the old selection score, provides prospective evidence.
- Counterfactual invariance is measured rather than enforced as a certification gate; two added-distractor cases can expose upstream G1 omissions, and action removal should not be treated as invariant.
- A high oracle coverage ceiling means gold edges occur in the candidate universe. It does not mean a gold-blind decoder knows which subset to choose.
- `G2_0088_NUMERIC_GATES_PASSED_NOT_CERTIFIED`, if reached, is a numeric development result, not authority to promote G2 or train G3. Independent native certification requires a separately ratified certificate.
