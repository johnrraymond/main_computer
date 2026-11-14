# G2-0023 — Contextual relationship representation (TRAIN-only architecture ablation)

## Hypothesis

G2-0022 found that matched ordinary fine-tuning explains most of the gain;
its additional hard-negative ranking loss adds little. G2-0023 tests whether
**candidate-specific relational context**, rather than loss reweighting or
extra parameters, improves selection of valid relationships under competition.
The existing scorer already has one attention-weighted, leave-one-out group
summary, so the experimental change must be genuinely different.

## Predeclared controlled arms

All arms share frozen G1 endpoints, G2-0004 rank/head and backbone; the
G2-0019 `precision_guard` scorer, top-24 **label-blind** candidate inventory,
source/relation group partition, and fixed 0.75 Bernoulli decision cutoff.

- `frozen_guard_0019`: G2-0019 precision guard, unchanged.
- `matched_finetune_0022`: previously trained ordinary fine-tuning control,
  loaded **unchanged** from G2-0022, with exact prior-case replay.
- `competition_aware_0022`: previously trained hard-negative arm, loaded
  **unchanged** from G2-0022 as a secondary historical comparator.
- `capacity_only_0023`: new residual branch using candidate-local transforms;
  same trainable parameter tensors and initialization as the next arm, but the
  new branch cannot see another destination's features.
- `relational_context_0023`: same residual branch and parameter count, but
  the candidate's query explicitly attends to the keys/values of **other**
  destinations belonging to the *same* source/relation, with learned
  relative-score bias and no self-attention.

The two G2-0023 arms start with an identical copy of the frozen G2-0019
scorer and the exact same newly initialized residual block. Their zero-initialized
residual readout makes their initial predictions exactly equal to the frozen
parent. Both arms use **320 steps**, learning rate **6e-5**, identical
label-blind training groups (the G2-0022 batch stream, 8 complete
source/relation groups for each chosen TRAIN case), identical optimization
and clipping, and exactly the G2-0019 `precision_guard` training objective.
No new ranking-loss weight is introduced. No DEV label can enter training.

Both models score each destination as an independent Bernoulli decision. No
softmax or one-destination constraint is applied to the outputs. Multiple
valid destinations are retained. Cross-group communication, self-injection of
competitor evidence for singleton groups, and gold-conditioned inference
candidates are forbidden.

## Ancestry and assessment

Reconstruct G2-0016–G2-0022 ancestry. Verify G2-0022's report contract,
compressed full per-case ledger, group/count/aggregate integrity, and both
control weight branches against their SHA-256s. Replay all **448 previously
exposed G2-0022 DEV cases** with the frozen parent and both saved controls;
abort on exact frozen/control graph or competition-diagnostic drift.

Primary assessment: **448 new prompt-disjoint DEV cases**: 320 independent
cases (seed `460028`) and 128 withheld-topology DEV cases (seed `460029`,
covering `2x4`, `4x2`, `4x4`). All prior TRAIN and DEV prompts from
G2-0016–0022 are excluded from both new cohorts. The sealed challenge/test
sets remain untouched.

Report micro F1, precision, recall, false positives, missing relationships,
exact graph cases, reachable-gold inventory, and source/relation competitors
that outrank correct edges. Disaggregate by family, density, and topology.
Compare the new relational arm to the **G2-0022 matched control** and to the
**capacity-only** arm; only the latter controls for added trainable capacity.
Diagnostic booleans are not approval or checkpoint selection. A better score
ordering without better graph F1 does not establish success.

## Run

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-contextual-relationship-representation --device cuda
```

Default immutable output:

`runtime/reports/gamer-v1/graph-g2-contextual-relationship-representation-0023/`

The directory contains:

- `g2_contextual_relationship_representation_report.json`
- `g2_contextual_relationship_representation_per_case.jsonl.gz`
- `g2_contextual_relationship_representation_development.safetensors`

The safetensors contains only **the two G2-0023 exploratory arms**. G2-0019,
G2-0022, G1, backbone and rank checkpoints are never replaced. An existing
output directory is rejected; for a new experiment use `--output` pointing
to a new directory. `--steps` must remain 320, and `--max-length` 2048.

## Local regression test

```powershell
python -m pytest -q nanojev/tests/test_nanojev_gamer_v1_g2_contextual_relationship_representation.py
```

These CPU tests cover prior-weight shape/identity, zero-initialized equality,
group-local and permutation-equivariant attention, self-only capacity control,
no singleton competitor evidence, TRAIN/DEV isolation, multi-positive
predictions, frozen G2-0019 state, exact historical replay rejection,
checkpoint SHA/key validation, immutable outputs and deterministic case ledgers.
Full CUDA training/assessment must be run where the historical runtime reports,
model weights and Qwen adapter exist.

## No promotion

This is a development experiment. It sets `g2_certified=false`,
`g2_production_unlocked=false`, `sealed_test_used=false`,
`challenge_or_test_split_used=false`, `production_promotion=false`,
`certification_authorized=false`, `model_selection=false`,
`checkpoint_selection=false`, `graph_execution_authorized=false` and
`memory_writes=false`.
