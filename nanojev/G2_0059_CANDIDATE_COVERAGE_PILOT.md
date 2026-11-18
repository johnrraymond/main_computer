# G2-0059 Candidate Coverage: Exact-Shard Diagnostic and Spatial G1 Recovery

## Scope

Research-only diagnostic for the first G2-0059 train shard, which captured 16 counterfactual pairs with only 3/16 pair coverage and 41 missing gold binding edges. Do **not** delete or overwrite the original shard. Do **not** restart the remaining 351 shards or disable the 98% training/validation coverage gate based on a pilot.

Static evidence from the expanded author and the original training compatibility inventory shows that the distractors use already-admitted graph signatures: `refers_to(action, action)` and `measures(action, quantity)`. The missing edges therefore cannot be explained by a new, unsupported type signature alone. The exact causes of the 41 missing edges (G1 token boundaries, low G1 scores, span budget, or candidate alignment) require a frozen-model replay of the real shard; a terminal aggregate alone is insufficient.

## One-command first-16-pair replay

Apply this patch at the `main_computer` repository root. From `nanojev`, run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale capture --device cuda --max-shards 1 --candidate-policy spatial --output ..\runtime\reports\gamer-v1\graph-g2-0059-spatial-pilot --compare-capture ..\runtime\reports\gamer-v1\graph-g2-0059-capture
```

The exact paths above resolve from the `nanojev` working directory. This command uses the **existing immutable plan**, replays its first 16 training pairs, and writes an **isolated** pilot shard. It compares against the original committed first shard, refusing if identities, hashes, or counts differ. No 0059 training or later shard is run.

The original `graph-g2-0059-capture` directory is never rewritten. Because the candidate-construction source changed and old shards are immutable, the old output directory must not be reused for this new policy.

## The tested candidate intervention

The original 0052 constructor selects the highest raw-G1 alternatives globally for each entity kind, then spends a fixed additional-anchor budget. The alternative uses the complete frozen G1 token lattice, with per-position and per-kind priority queues, while preserving every admitted 0051 anchor. It applies only generic whitespace/punctuation boundary normalization already present in the G1-to-G2 interface. It does **not** read the author graph, graph labels, decisions, family, outcomes, or any gold information to choose its candidates. Encoder weights and G1/G2-0052 parent artifacts are unchanged.

The post-construction TRAIN-only audit checks every missing gold binding by source span, destination span, relation type and origin (original graph versus added distractor). The stage classifications are:

- `unsupported_type_signature`
- `missing_endpoint` (with a nested cause of `raw_g1_favors_null`, `positive_raw_option_not_selected`, `punctuation_boundary_not_selected`, or `no_exact_or_punctuation_trimmed_token_boundary`)
- `inventory_or_alignment_defect`

Audit annotations are stored only in the shard metadata, not in inference features, and the `FeatureCorpus.pair(..., with_labels=False)` API does not expose them. The pilot also classifies the **original baseline failures**, not just failures left after the new policy.

## Outputs

- `graph-g2-0059-spatial-pilot/pilot-comparison.json`: 16-pair baseline versus new-candidate coverage with before/after cause summaries
- `graph-g2-0059-spatial-pilot/train-0000000.json`: per-graph detailed baseline and candidate failure cases, including exact spans and words
- `graph-g2-0059-spatial-pilot/train-0000000.safetensors`: label-blind frozen G2 candidate features
- `graph-g2-0059-spatial-pilot/train-0000000.labels.safetensors`: separate gold supervision

**Acceptance:** The 16-pair pilot must first be checked for causal error attribution and then reach at least the existing 98% floor (effectively 16/16 pairs). Any remaining failures must be corrected at their demonstrated causal layer. Pilot success does **not** authorize training: the full independent training and validation captures must meet the original gate.

If the pilot passes, use the spatial policy with a separate full capture directory and the existing immutable plan, then audit the complete training and validation cohorts before training. If it fails, inspect the before/after cause breakdown rather than increasing anchor budgets blindly.

## Verification completed locally

Executed mocked frozen-runner 16-pair replay, source-hash safeguards, actual real-data graph-author tests, synthetic raw-G1 spatial selection, post-construction failure classifications, and adjacent G2 CPU regressions. **No CUDA replay on the user's 16 recorded pairs was possible in this environment.** Reported 3/16 and 41 missing edges remain the original user's measurements, not new GPU results. No production promotion.
