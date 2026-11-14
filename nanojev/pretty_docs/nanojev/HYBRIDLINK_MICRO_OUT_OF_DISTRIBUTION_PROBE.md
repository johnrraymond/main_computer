# HybridLink anonymous micro-reduction: read-only OOD stress probe

**Status:** experimental diagnostic; no neural CUDA accuracy claim until a local checkpoint is probed.

The `hybridlink_micro_text_v1/best.pt` text adapter reached **72/72 valid independent micro-transitions and 12/12 six-operation free-running chains** on its existing DEV deck. This probe asks whether those skills transfer to unfamiliar expression structures, depths, signs, and magnitudes. **The original checkpoint, its configuration, and the sealed TEST split remain untouched.** No model parameter is optimized or modified.

## Minimal patch contents

- `tools/nanojev_hybridlink_micro_ood_probe.py`: entirely new read-only evaluation CLI, model-free preview, OOD deck generator and suite-scoped reporting.
- `tools/nanojev_hybridlink_micro_reduction.py`: one limited compatibility repair. When a model correctly computes a negative integer, Python parses `-7` as unary minus applied to `7`, while the teacher's internal tree can hold an integer literal `-7`. The old exact-AST comparison incorrectly rejects those equivalent forms. Canonicalize **only signed integer literals** while preserving all operation-tree and register checks. Complex unary-negated expressions remain disallowed.
- `tests/test_nanojev_hybridlink_micro_ood_probe.py`: generator, numerical invariants, train-overlap, negative-value, reversal, mocked inference, and checkpoint read-only tests.

## Controlled challenges

| Suite | Cases | Operations per case | Objective |
|---|---:|---|---|
| `control` | 12 | 6 | Re-run **the same** familiar DEV cases as a regression baseline |
| `structure` | 8 | 2/6/10/20 | Unseen AST shapes, different balanced/left/right/zigzag nesting and mixed arithmetic |
| `depth` | 8 | 2/6/10/20 | Test how chain reliability changes with length |
| `negative` | 6 | 6/10/20 | Force reductions that generate strictly negative intermediate values |
| `magnitude` | 6 | 2/6/10/20 | Increasing input magnitude: 8, 256, 4,096, 65,536, 1,000,000, **4,000,000** |
| `direction` | 6 | 6/10/20 | Three paired identical numeric problems with forward/reverse signed-subtraction directions |

All OOD problems preserve a tiny signed final residual (-4 through +4), independently of operand magnitude. The evaluation engine verifies that no new initial OOD state or whole-tree shape exactly duplicates a training case. The generator uses anonymous arithmetic registers only: no `Hawk`, `Dove`, or origin hints.

The full deck contains **46 scenarios** (12 familiar, 34 OOD) and at most **844 inference generations** for one control arm. Run a small subset first. The reported maximum is an upper bound because free-running chains stop on their first invalid step. You can also run individual suites to isolate failures. No TEST split is requested or consumed.

## 1. Install

```powershell
python .\new_patch.py `
  'C:\Users\subsi\Downloads\hybridlink_anonymous_micro_ood_probe_patch.zip'
```

Use an already installed `hybridlink_micro_text_v1/best.pt`. **Do not retrain before this OOD test.**

## 2. Preview (CPU, without loading Qwen or checkpoint weights)

```powershell
& $py -m tools.nanojev_hybridlink_micro_ood_probe `
  --stage preview --device cpu `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt'
```

The preview reads the checkpoint's adjacent `run_config.json` to recreate and verify the original training deck; it does **not** load or change any model weights. It prints `MICRO_OOD_PREVIEW` with per-suite counts, total operation budget, model-free examples, and a reproducible OOD-deck hash.

## 3. Smoke: one scenario per suite (six cases)

```powershell
& $py -m tools.nanojev_hybridlink_micro_ood_probe `
  --stage probe --device cuda `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --suites control,structure,depth,negative,magnitude,direction `
  --limit-per-suite 1 `
  --controls text_only `
  --progress-every 5 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_ood_smoke_v1'
```

This smoke includes only the **first** scenario in each suite. It does not demonstrate competence at the 20-operation or four-million boundaries; use the complete `depth` and `magnitude` suites for those.

## 4. Target the extremes immediately

```powershell
& $py -m tools.nanojev_hybridlink_micro_ood_probe `
  --stage probe --device cuda `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --suites depth,negative,magnitude `
  --controls text_only `
  --progress-every 10 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_ood_hard_v1'
```

## 5. Full control + OOD comparison

```powershell
& $py -m tools.nanojev_hybridlink_micro_ood_probe `
  --stage probe --device cuda `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --suites control,structure,depth,negative,magnitude,direction `
  --controls text_only `
  --progress-every 10 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_ood_full_v1'
```

Optional `--controls text_only,zero_latent,intact` evaluates three inference controls but triples the upper inference-call budget. The saved TEXT checkpoint's HybridLink head is **not trained on micro-reductions**; these optional arms are only diagnostics and must not be interpreted as a trained micro-HybridLink benefit.

## What gets scored

At every teacher-provided intermediate state, the model attempts the **next single operation**; this is the independent one-step score, not a chain. Separately, an entirely free-running chain feeds back each model-generated state to the next invocation. The chain stops immediately on the first invalid response. A validator checks one correct operation, immutable untouched AST subtrees, unchanged register names, preserved subtraction direction, and exact signed answer preservation. It permits a different **valid** reduction order.

Outputs:

- `result.json`: `score_by_suite`, `score_by_depth`, `score_by_scale`, failure examples, deck hash, checkpoint hash, exact call count, status.
- `one_steps.json`: every independent one-step attempt, expected state and generated state, plus why a step failed.
- `free_chains.json`: model-only trajectories, first failure point and terminal signed-answer retention.
- `cases.json`: exact anonymous challenge inputs to reproduce any failure.

**No acceptance gate is relaxed.** Successful OOD cases remain diagnostic until proper independent certification, broader expression and arithmetic coverage, and analysis of numerical-vs-structural failure rates. A small OOD suite is a stress test, not a population-wide generalization guarantee.
