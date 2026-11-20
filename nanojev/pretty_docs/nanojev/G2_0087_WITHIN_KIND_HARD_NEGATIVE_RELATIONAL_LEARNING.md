# G2-0087 — Within-kind hard-negative relational learning

## Scope

Continuation of the frozen G2-0083 head `8ba554eb5679faf68ba065fc410a61f28862a36f6b2a0bc3912ff1278a86a85e`. Its G1-0082 composite interface, native graph head, certified source files, and G1 parameters remain unchanged. G2-0083 remains saved and independently usable. This is an **experimental G2 training candidate**, not G2 certification or G3 authorization.

The G2-0086 within-type causal report found only 113/314 source- and destination-kind-matched groups where every true destination outranked every false destination. Overadmission was also large. G2-0087 directly trains that distinction instead of using decoder-only calibration or training again on the original sampled pairwise objective.

## Changed learning objective

This is an opt-in new runner, not a modification to `nanojev_gamer_v1_graph_curriculum.py` or the G1-0082 hash-bound source. The native G2 head receives only **G1-predicted spans**. Gold annotations are aligned to those observed spans solely for training supervision. A gold node missed by G1 is not teacher-forced into the graph.

The new objective combines (1) positive-edge classification, (2) dense negative-edge classification across **every directed observed-object pair**, (3) positive-versus-null margin, (4) source- and destination-kind-matched hard-negative ranking for each reachable gold binding, and (5) penalty on the most strongly admitted false pairs. A hardest incorrect candidate must match source object, destination object kind, and supervised binding label; the trainer never silently treats a different true binding label as a negative for its own class. The coefficients, training steps, seed, optimizer, and hidden-state budget are declared in source and locked into the prospective seal. No sealed development/challenge labels are used for training, calibration, or model selection.

## One-command execution

From `C:\Users\subsi\main_computer\nanojev`, after applying the ZIP at the `main_computer` root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_hard_negative_learning_0087 run --device cuda
```

The runner requires the existing successful G1-0082 certification, retained G2-0083 training head and failed acceptance report, and the G2-0085 prompt-exclusion record. In order, it:

1. Verifies the source checkpoint and G1 certificate lineage, creates a fresh 96-development/64-challenge author-annotated cohort, excludes previous curriculum and previously inspected prompts, and **commits answers before training**.
2. Captures frozen Qwen and composite G1 predictions for the original training split, then updates only the previously authorized G2 modules for 400 steps.
3. Saves a new G2 head and verifies bit-for-bit preservation of every non-G2 weight against the original G2-0083 head.
4. Runs the original G2 80/32 development/challenge evaluations, both G2 ablations, and G1 current-weight retention, then issues the original **developmental** gate report, even when it fails.
5. Performs blind native inference with the new G2 and the unchanged G2-0083 control **on exactly the same predicted G1 objects**, plus untrained, zero-hidden, and no-binding controls. It saves every directed candidate's non-null-versus-null score margins and hashes the prediction lock.
6. In a separate scoring process, opens gold answers for the first time, verifies evidence hashes/bijections, independently computes binding exact-set rates, edge precision/recall/F1, G1-unreachable edges, paired exact-case gains/losses, and within-kind ranking improvements. Applies the original G2 ≥80% dev / ≥70% challenge / ≥8 percentage-point causal gains. Both original G2 numerical gates and **strict** source-matched ranking improvement relative to G2-0083 must pass for the experimental numeric result to be positive.

The prospective generator is the same author-marked distribution as the previous G1/G2 experiments; a pass does not demonstrate different-distribution generalization. The experiment does not create a native G2 certificate, unlock G3, or change production code.

## Outputs

`..\runtime\reports\gamer-v1\oracle-fabric-g2-hard-negative-learning-0087\`

- `seal/commit.json`, `prompts.jsonl`, `answers.SECRET.jsonl` — pre-training sealed commitment; do not share `answers.SECRET.jsonl` before the blind lock.
- `training/gamer_graph_head.safetensors`, `training/manifest.json`, `training-integrity.json` — candidate G2 checkpoint, source hashes, and frozen-parameter verification.
- `evaluation/G2_dev`, `G2_challenge`, `G1_dev`, `G1_challenge` and `historical-development-gate/certification.json` — historical numerical and causal gates, current G1 retention.
- `blind/predictions.jsonl`, `blind/lock.json` — frozen and hashed blind predictions including G2-0083 control and raw pair margins.
- `scored/report.json`, `scored/cases.jsonl` — independent prospective scoring and failure attribution.

The output directory must not exist when `run` begins. A failed execution preserves work already written; **do not delete frozen evidence or rerun training just to resume**. If training and checkpoint integrity have completed, the remaining explicit phases are:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_hard_negative_learning_0087 history --device cuda
& $py -u -m tools.nanojev_gamer_v1_g2_hard_negative_learning_0087 predict --device cuda
& $py -u -m tools.nanojev_gamer_v1_g2_hard_negative_learning_0087 score
```

Use only the phases not already completed. Each refuses an already completed immutable output. `score` also requires and validates the current head's original historical-development/retention gate report. All original oracle/certification thresholds remain unchanged.

## Local verification

The runner was regression-tested with a real tiny native head's G2-only gradient update, strict gold-to-observed node mapping, a hard-negative candidate beyond the legacy truncated negative sample, a sealed-score synthetic end-to-end audit, checksum tampering, false labels, missing commits, and no duplicate scoring. G1-0082/G2-0083 through G2-0086 adjacent tests were also exercised. The CUDA continuation on the user's saved checkpoint has **not** been run as part of preparing this patch; no accuracy improvements are claimed before that execution.
