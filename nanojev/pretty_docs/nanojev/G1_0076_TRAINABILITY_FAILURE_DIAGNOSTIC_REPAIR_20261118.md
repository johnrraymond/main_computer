# G1-0076: One-case trainability failure — corrective diagnostic revision

## Observed failure

On the user's real frozen-G1 evidence run `oracle-fabric-g1-discrimination-trainability-bakeoff-0001`, both legacy and cutover first-case training had `unreachable_gold=0`, while every exact-case check remained zero.

- Legacy first case: 31 positive candidates, 256 sampled hard negatives; hard-loss 1.313815 → 0.007721, exact 0/1.
- Cutover first case: 30 positive candidates, 256 sampled hard negatives; hard-loss 1.215405 → 0.004933, exact 0/1.
- Both structured follow-up runs registered 28 structured violations and exact 0/1 throughout, so G1-0076 correctly blocked further training and did not unlock G2.

The tiny pointwise loss **does not establish whole-set trainability**: the original loss samples only 256 negative options and is not a measure of all decoded errors. These observations cannot alone distinguish feature insufficiency from unmined negatives or incomplete optimization.

## Verified implementation defect

The structured trainer kept a model snapshot only when its **number of exactly reconstructed cases** improved. If every epoch remained at zero exact cases, it retained epoch 1 and discarded all subsequent progress, even if correct objects increased or false objects decreased. This could make its returned model worse than its later learned states. This patch corrects that deterministic selection defect.

## New training-only diagnostic interventions

1. Score each intermediate training model lexicographically by exact sets, correctly reconstructed objects, fewer false objects, and fewer missing objects. Keep the best actual model, including epoch 0 if training regresses. Log each epoch's quality and the selected epoch. All metrics here use TRAIN labels only.
2. After initial hard-negative fitting, identify **incorrect candidates selected during label-blind inference** and force them into the training negative pool. Repeat for up to three rounds, retaining the best prior model on regression. This is counterexample-guided supervision; the oracle is used only to label training examples, never to choose candidates or objects during inference.
3. Run structured whole-set fitting from the best of the original and counterexample-mined models, with a longer diagnostic fit budget on the one-case and eight-case ladders.
4. If a trainability gate still fails, emit the per-training-case correct/missing/false counts, per-epoch quality, mined negative counts, and the winning fit stage. Do not run or select full-cohort competitors.

The canonical seven-kind fix from the previous patch remains in place. Existing checkpoint, adapter, candidate-source and historical control parity checks remain unmodified.

## Guarantees and limits

- The 1-case / 8-case requirement is **not weakened**. A case passes only if all its gold objects and no false objects are selected.
- The 48 TRAIN / 16 SELECTION / 16 AUDIT partition and historical challenge handling are unchanged.
- Frozen Qwen, LoRA, legacy G1, and deterministic Oracle-Fabric G1 checkpoints are not modified; the runner trains diagnostic interface coefficients only.
- No gold labels are used in candidate generation or the model prediction path. Training labels supervise hard negatives, counterexample mining and train-only snapshot selection.
- A blocked run still leaves the G1-0074 control selected, prohibits production promotion and keeps G2 blocked.
- The repair is **not evidence of a successful live trainability test**. If a real 1-case gate still fails, that is useful new evidence about the remaining scoring or representation limits; the expanded report will identify the actual missed and false objects.

## Run the repaired experiment

Apply the patch at the `main_computer` root, then run from `main_computer/nanojev`. Use a **new output directory**, because the failed run already wrote an immutable result directory.

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --structural-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-structural-learning-bakeoff-0001 `
  --learned-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-learned-interface-bakeoff-0001 `
  --coverage-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-candidate-coverage-bakeoff-0001 `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-discrimination-trainability-bakeoff-0002 `
  --device cuda
```

## Verification on patch source

- Focused G1-0076 tests, including seven-kind regression and new counterexample/snapshot tests: 16 passed.
- Adjacent G1-0072–0076 and oracle-decoder test collection: 68 passed.
- No real frozen-checkpoint CUDA rerun was available locally; success of the repaired candidate cannot be claimed.
