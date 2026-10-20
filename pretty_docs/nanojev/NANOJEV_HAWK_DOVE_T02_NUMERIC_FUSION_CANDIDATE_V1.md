# NanoJev Hawk–Dove T02 Numeric Fusion Candidate V1

## Purpose

Test whether a **separate numerical input channel**, coupled to the **existing frozen V3 symbolic representations**, can pass T02 without embedding the Hawk–Dove arithmetic coefficients in source code. This is a forked *candidate*, not a change to the donor checkpoint, the production trainer, any Hub worker, or the T00–T08 gate definitions.

### Starting observations

- Frozen-language-feature numerical readouts: best previous T02 development accuracy ~33.15% (two-hot).
- Corrected scientific V3 (10M decode cap): 22.83% joint development accuracy.
- A TRAIN-only affine control using exact numerical operands: 100% joint development accuracy.
- The previous pathway probe did **not** prove that V3 independently discards information; it demonstrated limited operand recovery from the tested frozen feature planes.

## Candidate architecture

```text
Validated raw T02 prompt
    |-- strict Lisp AST & scientific-bar validation
    |    extracts exactly a,b as decimal integers (never the displayed Y)
    |    asserts T02 operation structure, operand payoff roles, and a+b=1,000,000
    |    |
    |    `---- learned numeric affine XY readout  (float64)
    |                  |
    `---- frozen Qwen -> frozen parent -> frozen pretrained V3 body
                            |
                            `---- learned, regularized residual head
                                      |
                       continuous signed XY (float64 output)
```

**Both X and Y coefficient vectors are fit by least squares using TRAIN pairs only.** The source code does not use `X=3b-a`, fixed payoff values, or `Y=1.5b` when producing answers. It validates an AST grammar and binds operands by payoff role, not by their position in text. This is role-aware symbolic *parsing*, not a learned grammar or generalized arithmetic rule engine.

The pretrained V3 features are retained as a 384-dimensional concatenation of two 192-dimensional slot representations and available to the small learned residual branch. The numerical affine branch is fit on **float64** exact integer input counts; this prevents float32 rounding from falsely failing cases with X=0 under the existing 5%-relative error criterion. The original 70% T02 gate is unchanged.

**Important scientific limitation:** With the current teacher T02 has only *one* fixed arithmetic rule. The numeric readout is sufficient to fit it, so the symbolic residual is not required. Even if the fusion passes, that alone is **not** evidence of symbolic generalization or correct switching between different arithmetic expressions. The experiment explicitly reports the effect of zeroing and shuffling the V3 features and zeroing the numerical inputs to expose this. Further cross-rule tests need genuine varying-rule training and withheld-rule cases, not synthetic target leakage.

### Reused, immutable artifacts

- Prior features `hawk_dove_t02_continuous_benchmark_v1/features-{train,dev}.npz` and case orders.
- Pathway capture `hawk_dove_numeric_pathway_probe_v1/pathway-{train,dev}.npz` for correctly capped baseline outputs.
- Original `hawk_dove_million_scientific_v2/latest.pt`, scientific teacher SQLite, and Qwen source.

All inputs are SHA-256 pinned in the new manifest. Existing artifacts are never overwritten. The **test split is never read** by this experiment. Restarting an existing run reuses the frozen candidate only if the manifest and checkpoint hashes match; identity drift stops the run.

## Install

From `C:\Users\subsi\main_computer`, unzip the patch and copy only the contained overlay files:

```powershell
Expand-Archive `
  .\nanojev_hawk_dove_t02_numeric_fusion_v1_patch.zip `
  .\_numeric_fusion_patch -Force

Copy-Item .\_numeric_fusion_patch\main_computer_test\* . -Recurse -Force

$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_hawk_dove_numeric_fusion_v1 -v
```

### Run candidate and real-prompt smoke

```powershell
& .\tools\run-nanojev-hawk-dove-numeric-fusion.ps1 `
  -Stage All `
  -Python $py `
  -Device cuda `
  -Epochs 60 `
  -LiveCases 2
```

`All` executes prepare → train → evaluate → live. Training/evaluation uses **already-cached representations**, not a million-step retrain. The `Live` stage is the only stage that needs Qwen/CUDA: it evaluates 2 independent development cases across all eight masks using **fresh prompts through frozen Qwen and V3** and checks those features and predictions against the cached records.

For CPU-only fitting and evaluation with no Qwen startup:

```powershell
& .\tools\run-nanojev-hawk-dove-numeric-fusion.ps1 -Stage Prepare  -Python $py
& .\tools\run-nanojev-hawk-dove-numeric-fusion.ps1 -Stage Train    -Python $py
& .\tools\run-nanojev-hawk-dove-numeric-fusion.ps1 -Stage Evaluate -Python $py
```

To recheck live behavior later:

```powershell
& .\tools\run-nanojev-hawk-dove-numeric-fusion.ps1 `
  -Stage Live -Python $py -Device cuda -LiveCases 2
```

## Outputs

Under `C:\Users\subsi\NanoJev\runs\hawk_dove_numeric_fusion_v1`:

| File | Contents |
|---|---|
| `manifest.json` | Exact code, dataset, donor, teacher, and checkpoint provenance |
| `candidate.pt` | New T02-only numeric fusion head, fitted numeric weights and trained symbolic residual |
| `candidate.json` | SHA-256 of candidate checkpoint and run identity |
| `results.json` | Train/dev scores, per-mask scores, ablation effects, learned coefficient matrix, 70% gate |

**Score definitions:** both X and Y within 5% of their true values; secondary `both_within_5000` absolute tolerance. For an exactly zero target, `accuracy()` uses a unit denominator, so float64 numerical inputs and outputs are essential.

### Interpret the results

1. **Fusion passes, and numerical-input ablation fails:** The integration proves the learned numerical readout can use exact inputs to solve T02, not that it learns all future operations.
2. **Semantic shuffle/zeroing changes accuracy:** Evidence that the current residual uses V3 features on T02. Measure magnitude of effect and generalization on withheld cases.
3. **Semantic shuffle/zeroing has no effect:** The current head solves this fixed rule using numeric inputs alone. This is a *successful precision repair candidate*, but no evidence of learned symbol-dependent operation selection. The next step is varying-operator tasks.
4. **Numeric-only passes, fusion fails:** Residual overfitting or optimization regression; do **not** promote fusion merely because it uses extra inputs.
5. **Live smoke fails while cached evaluation passes:** Runtime-integration defect, possibly token capture, donor drift, parser mismatch, or numeric dtype conversion.

No production promotion is automatic. No changes to checkpoint lineage, Hub worker, other tiers, source model, existing reports, or curriculum gates.

## Scope limits

- This prototype accepts only the known T02 grammar. New payoff symbols, changes in the operator, or nonconserved counts are rejected, not interpreted or guessed.
- The parser reads **counts** from the expression and validates a given `Y` structurally, but does not provide either target X or Y to the model.
- The fitted affine coefficients are not uniquely identifiable outside the `a+b=1,000,000` training support. Success on T02 does not imply accuracy when that conservation constraint or payoff mapping changes.
- If a V3/Qwen feature actually has unique useful symbolic information, a separate task with *varying semantic rules* will be needed to identify its causal contribution.
