# G2-0090 — Progressive exact-graph trainability and interference diagnosis

## Purpose

G2-0089 exactly memorized one graph (1/1 at 140 updates) but ended the independent eight-case restart at 1/8 exact after 800 updates, despite a declining objective. G2-0090 diagnoses this failure without starting another population bakeoff or altering the certified G1 interface, previous G2 checkpoints, or original certification gates.

The experiment reuses **the exact same eight fully G1-reachable, oracle-decodable training prompts selected and hashed by G2-0089**. It verifies their identities, the G2-0087 parent checkpoint, the G2-0088 frozen decoder, and the G2-0089 failed trainability record before running. Gold labels are used only for trainability diagnostics and the training objective, never for inference-time selection.

## Execute

Apply the patch ZIP at the `main_computer` repository root. Run from `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_trainability_diagnostic_0090 run --device cuda
```

If a complete **stage** has already been committed and a later stage was interrupted, resume without repeating completed stages:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_trainability_diagnostic_0090 resume --device cuda
```

The default experiment has update budgets **300, 450, 600, 1000** for the 1-, 2-, 4-, and 8-case stages, respectively. For **diagnostic-only** short smokes the run command also accepts `--step-limits 1,1,1,1`, or any four positive integers up to 5000. The resulting evidence is bound to those selected budgets, not equivalent to the default experiment.

## Progressive procedure

1. Recover the same eight training cases from G2-0089, using native *predicted* G1 objects and the identical frozen G2-0088 joint decoder. Validate that every gold edge is reachable and that the decoder can represent the correct set.
2. Load a disposable G2-0087 head. Only `binding.`, `pair_projection.`, and `interface_couplers.G1_G2.delta.` are trainable. The frozen Qwen backbone, G1 and all later graph stages are unchanged.
3. Memorize prefixes of **1 → 2 → 4 → 8** cases. Unlike G2-0089, carry the **best model weights and matching AdamW optimizer moments** into each expanded stage. Do not restart from the parent at stage two.
4. Evaluate true decoded complete-graph exactness every ten steps (including step zero). Measure true/false edges, within-kind misrankings, per-case objective, and decode errors. Select the best stage checkpoint lexicographically by **exact graph count**, then edge-set Hamming errors, then mean objective. Recover that checkpoint, not the last update.
5. Compute per-case gradients of the exact same objective and their complete pairwise cosine matrix at stage starts and periodically during optimization. Negative cosine implies opposing first-order gradient directions, but not necessarily a consequential regression.
6. At stage boundaries, also make **reversible, normalized one-step weight perturbations** trained on one case at a time. Re-evaluate all other cases, recording actual loss increases and decoder changes. Restore the original weights in a `finally` block. These counterfactuals never modify an upstream checkpoint or retained experimental checkpoint.
7. Record when loss falls without exactness improving, when exactness falls despite falling loss, and when a previously exact graph loses exactness. Distinguish transient best exactness from the actual final and restored checkpoints.

A stage can stop early after two successive sampled exact-set passes. The saved best model remains the actual version that achieved the best lexicographic outcome. The 8/8 gate is considered *demonstrated* only when all eight are reconstructed using the real native head and frozen decoder at the same checkpoint.

## Evidence files

Output directory: `../runtime/reports/gamer-v1/oracle-fabric-g2-progressive-trainability-0090/`.

- `report.json`: four-stage summary, parent hashes, counts, regression diagnostics, and mandatory `g3_unlocked:false`.
- `provenance.json`: input / runner / decoder / eight-prompt identity commitments.
- `stage-{1,2,4,8}/checkpoint/`: best disposable graph head and manifest; G1 retention verified against G2-0087 parent using the existing immutable stage comparison.
- `stage-{1,2,4,8}/optimizer.pt`: optimizer moments corresponding exactly to the best checkpoint, for progressive continuation or resumed execution.
- `stage-{1,2,4,8}/history.json`: per-case loss, errors, ranking inversions and exactness at each evaluation point.
- `stage-{1,2,4,8}/gradients.json`: per-case gradients' cosine Gram matrices and reversible intervention results.
- `stage-{1,2,4,8}/summary.json` and `lock.json`: stage results and SHA-256 tamper commitments.

The `resume` command checks the source, input, selection, head, optimizer, and stage hashes and refuses to treat incomplete or modified stages as committed.

## Interpretation and hard constraints

- Passing 1/1 but failing 2/2 or 4/4 suggests a progressive interference or optimization limitation; use gradient cosines and actual cross-case perturbations to distinguish possibilities.
- A large positive gradient cosine without improved exactness instead directs attention to objective/decoder mismatch or insufficient representational capacity.
- A graph becoming exact and then inexact despite decreasing loss is strong evidence that training objective progress is not aligned with the target complete-graph decision.
- An 8/8 result proves **training-set memorization only**. It does not demonstrate generalization, G2 certification, independent-distribution performance, or cause a G3 unlock.
- If 8/8 fails, do not proceed to another 400-step population bakeoff without diagnosing the stage reports. If 8/8 passes, separately design the structural-learning continuation and properly sealed development evaluation.

No fresh gold-answer seal is needed for this explicitly training-only diagnostic; no certificate is written. All reported G2 certification and G3 unlock fields remain false.
