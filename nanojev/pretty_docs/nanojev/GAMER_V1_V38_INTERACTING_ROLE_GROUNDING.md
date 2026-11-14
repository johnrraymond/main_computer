# GAMER V38: interacting-role semantic grounding

## Why this version exists

V37 returned 104/120 complete semantic structures, down from 106/120 in V36. Its role score was a sum of four individually computed clause scores. Cross-entropy over 24 permutations **did not** allow a clause's interpretation to depend on the other clauses: the representation was factorized. This is the architectural defect addressed here.

## Single experimental change

Four frozen-Qwen clause representations pass together through a shared, **permutation-equivariant** four-token attention block. Learned attention supplies cross-clause information to each role scorer. The output remains the same four-by-four role logits and the same 24-permutation assignment loss used in V37. No index embedding is available to memorize a fixed ordering of clauses. The C/D probability-referent head remains separate and unchanged in form.

Unchanged: raw prompt to frozen Qwen capture, numeric-literal transcription with no semantic assignments, V36 clause endpoints, 480 FIT / 120 DEV cases, seed 35, V34 frozen numerical head, labels, 5 completely sealed held-out template families, development and test thresholds, production authority, and Space Captain tactical-v6.

The role-head inputs are learned Qwen embeddings, never text-template IDs, labels, correct roles, or expected actions. Numeric transcription still reads exactly four integer amounts and one probability fraction; it **does not** interpret their meaning. This is still a narrow, explicit two-action game grammar, not unconstrained language understanding or end-to-end tactical competence.

## Falsification and controls

- V37 control: the derivative of slot 0's role score with respect to another clause is exactly zero.
- V38 intervention: this derivative is nonzero and the attention parameters learn under the same permutation loss.
- V38 slot-shuffle equivariance: permuting input clauses merely permutes output role logits.
- Same FIT source hash, unchanged Qwen capture bytecode, unchanged test thresholds.
- Fixed failure protocols and one-use sealed holdout remain unchanged.
- Real CUDA success is **not** established by local PyTorch tests.

## Run

```powershell
& $py -u -m tools.nanojev_gamer_v1_v38_interacting_role_grounding run `
  --run "runtime\reports\gamer-v1\transition-005-v38-interacting-role-grounding-0001" `
  --v34-run "runtime\reports\gamer-v1\transition-005-v34-grounded-learning-0001" `
  --device cuda
```

The program stops before opening the 200 held-out cases unless the DEV gates all pass: complete semantic structures >=108/120; final decisions >=108/120; pairs >=54/60; decisions exceed numeric-only ceiling. A passing DEV is necessary, not sufficient, for generalization. `dev_failure_diagnostics.json` enumerates wrong assignments from **DEV only**; the gold semantic comparison there is diagnostic, not a prediction input.

Nothing in this patch authorizes operational promotion or tactical orders. Keep V36 and V37 run directories intact as controls.
