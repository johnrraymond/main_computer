# G2-0091 — frozen trainability generalization test

## Objective

Measure whether the G2-0090 stage-8 checkpoint, which memorized eight *allocation*
training graphs, actually improves unseen exact graph reconstruction. Compare the
same predicted G1 objects using **four** predeclared conditions:

- `g2_0087_native`: frozen G2-0087 weights with original native decoder.
- `g2_0088_joint`: the **same** G2-0087 weights with the previously selected,
  frozen G2-0088 joint graph assembler.
- `g2_0090_native`: frozen G2-0090 stage-8 weights with native decoder.
- `g2_0090_joint`: frozen G2-0090 stage-8 weights with **the same G2-0088** joint
  graph assembler used in the trainability study.

No learning, hyperparameter search, changed decoder policy, or G1 mutation.
All four conditions consume exactly the same G1-0082 predicted nodes on each
prompt. Frozen checkpoint, provenance, manifest, and stage-8 optimizer/history
lock hashes are validated before running.

## Running

Apply at the `main_computer` repository root. From `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_frozen_generalization_0091 run --device cuda
```

Run is a subprocess sequence: `prepare` writes a gold commitment and
checkpoint/decoder lineage; `predict` sees only the public prompts and locks the
predictions; `score` checks all commitments and independently replays the frozen
joint decoder from raw pair scores. If `prepare` succeeded but inference was
interrupted **before** its blind lock was written, delete only the incomplete
G2-0091 `blind/` directory before rerunning `predict`. Do not overwrite or alter
any earlier-stage evidence.

Explicit split commands for a prepared output:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_frozen_generalization_0091 predict --device cuda
& $py -u -m tools.nanojev_gamer_v1_g2_frozen_generalization_0091 score
```

## Cohorts and measurements

- **Fresh same-generator:** 96 development and 64 challenge cases, alternating
  `encounter` and `allocation`. Prompt digests are excluded from earlier train,
  G1/G2 experiments and the G2-0090 eight memorized cases.
- **Historical:** original 80 development and 32 challenge examples, reported
  separately from fresh evidence.
- **Counterfactual:** eight new base/fuzz pairs (16 inputs): renaming, role
  swap, distractor, and action removal. Author-ID normalized graph deltas are
  compared to *gold-defined transformations*, not blanket invariance.

Reports include complete-edge-set exactness, edge precision/recall/F1,
family-specific metrics, G1-exact counts, gold-graph G1 reachability, paired
wins and losses, control ablations, and counterfactual delta equivalence.
Source/destination span alignment is checked before scoring. Oracle answers
are used **only after** blind inference is committed.

### Result files

`runtime/reports/gamer-v1/oracle-fabric-g2-frozen-generalization-0091/`

- `seal/commit.json`, `seal/prompts.jsonl`, `seal/answers.SECRET.jsonl`
- `comparison.json` — frozen four-arm design.
- `blind/predictions.jsonl`, `blind/lock.json` — raw logits, anchored edges,
  and hash-locked blind model outputs.
- `scored/report.json`, `scored/cases.jsonl` — metrics and per-case errors.

## Interpreting the result

The stage-8 model was trained on eight allocation-family examples. Strong
memorization performance does not imply generalization; special attention
should go to the held-out encounter family and exact-graph gains/losses.
Comparison with G2-0088 is a matched **decoder-control** test, not a comparison
against different test cases or checkpoint updates. Fresh examples are from the
same author generator, so even strong results are not independent-distribution
certification.

### Certification policy

G2-0091 is a **diagnostic**. It never changes original G2 acceptance thresholds
or any checkpoint/certificate, and it always reports `g2_certified: false`,
`g3_unlocked: false`. A successful G2-0091 evaluation can guide a controlled
structural-learning generalization experiment, not unlock G3.
