# G1-0077 — Prospective Frozen-Interface Generalization Test

## Purpose

G1-0076 demonstrated one-case and eight-case trainability, then selected `staged_joint_64` for **both** G1 checkpoint lineages. On previously examined cohorts, Oracle-Fabric achieved 59/80 retrospective development exact cases, 13/16 audit exact cases, and 25/32 historical challenge exact cases. Those cohorts cannot be presented as newly sealed evidence.

G1-0077 tests the already fitted **G1-0076** interface, including its exact trained `staged_joint` coefficients, against **160 newly generated examples** using the source corpus generator's previously reserved `test` vocabulary. No training, parameter search, model selection, threshold adaptation, or G2 action is part of this protocol.

## Outputs and the seal

This is a three-stage, precommitted test. Every output path is write-once.

1. `prepare` validates G1-0076's frozen research report and original data lineage; makes **160 newly generated, exact-prompt-disjoint** oracle-annotated test examples; writes only `case_id`, `prompt`, `family`, and a prompt digest to `prompts.jsonl`; separately commits a SHA-256 digest of `answers.SECRET.jsonl` in `manifest.json`. It records the runner and generator source hashes, selected decoder, checkpoint digests, and frozen-interface report hash.
2. `predict` reads **only** `prompts.jsonl` from the new cohort; checks the code hash, original data/checkpoint/reference lineages, and frozen coefficient hashes; replays both G1 checkpoints, the fixed G1-0074 structured-set control, and G1-0076's `staged_joint_64` using saved coefficients. It writes `predictions.jsonl` and its digest. **The answer file is neither opened nor parsed by this command.** No optimizer is instantiated.
3. `score` first validates the locked prediction ledger and matching prompt hashes, then opens the answers file for the first time, checks the answer precommitment, and scores both decoders. It writes a scored report and `UNSEALED.json` to prevent repeat scoring of that seal.

**Important limitation:** A cryptographic hash commitment is not an independent external custodian. The plaintext answer file exists locally; the operator must not open or inspect it before scoring. The test demonstrates prospective prompt-disjoint generalization **within the known generator and families**. It does not demonstrate transfer to an independent linguistic distribution or constitute official G1 certification. A test failure must lead to a *new* precommitted cohort after changes, never retuning on the current cohort and reporting that as sealed.

## Commands (from `main_computer\nanojev`)

Run these in order, keeping the generated `answers.SECRET.jsonl` unread until the third command.

### 1 — Prepare sealed cohort and commit answers

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_sealed_generalization_0077 prepare `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --discrimination-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-discrimination-trainability-bakeoff-0002 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --seed 760077 --cases 160
```

### 2 — Frozen blind inference

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_sealed_generalization_0077 predict `
  --seal ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --structural-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-structural-learning-bakeoff-0001 `
  --learned-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-learned-interface-bakeoff-0001 `
  --coverage-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-candidate-coverage-bakeoff-0001 `
  --discrimination-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-discrimination-trainability-bakeoff-0002 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077-blind `
  --device cuda
```

### 3 — One-time scoring and unseal

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_sealed_generalization_0077 score `
  --seal ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --predictions ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077-blind `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077-scored
```

## Results and interpretation

The scored report includes paired G1-0074 control and G1-0076 structured-interface exact rates, object micro- and macro-F1, required semantic type recall, new false objects, exact-set gains, and **Wilson 95% confidence intervals** for exact reconstruction. The numerical research check uses the existing G1 requirements of 80% exact sets, 90% macro-F1 and 82% actor/action/quantity recall. Passing those numbers on this cohort is **not** equivalent to the existing formal G1 gate: it does not re-run hidden-state/component causal ablations, independent stage certification, or the full G1–G7 acceptance suite.

A strong prospective result supports keeping the staged discriminator/structured-set architecture as a G1 candidate. A failure identifies a generalization limitation and invalidates the *current hypothesis* that earlier gains transfer. Either way, the old seal is consumed and cannot be legitimately repurposed for model selection.

## Nonmutation and source scope

No existing production file, trained G1 checkpoint, source LoRA, G2 module, or stage gate is modified. The experiment installs only the new research runner, tests, and this `pretty_docs` protocol. The ZIP has no Markdown at its root. The actual GPU evaluation must run on the user's environment where the frozen Qwen/LoRA and trained G1 checkpoints reside.
