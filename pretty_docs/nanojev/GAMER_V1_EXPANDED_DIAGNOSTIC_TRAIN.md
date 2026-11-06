# GAMER V1 — 100-vector expanded-head diagnostic training

## Purpose

The original expanded 100-vector head has approximately 430 million parameters, while its certified source provides 1,752 train and 438 dev examples. A rising development MAE does **not** by itself establish overfitting, FP16 update loss, unhelpful contextual vectors, or a broken LoRA. This is a diagnostic laboratory to distinguish those explanations without modifying the source run.

## Run (after stopping the original expanded-head trainer)

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head-100-diagnostic-train `
  --run runtime\reports\gamer-v1\transition-005-expanded-head-100-0001 `
  --device cuda
```

`--run` points to the **expanded-head run**, not the original certified run. If it has a custom name, also give `--expanded-diag-source-run runtime\reports\gamer-v1\transition-005`.

The diagnostic **must not be run concurrently with training or resume** of the source expanded run. It validates the checkpoint and adapter hashes again before writing its final report and fails if the source has changed.

The default is 12 short optimizer steps per branch (mixed continuation, quarter learning-rate continuation with the same mixed minibatches, exact-zero/small-sign focus, and isolated `captain_crew_risk`). The branches start from the **exact saved latest model, AdamW optimizer moments, LR, loss-scaling and RNG state**. These experiments use the original trained 100-vector, 4,096/2,048/512/128 C0 head, the original frozen Qwen/LoRA feature caches, and the original loss. They cannot update production checkpoints.

To increase evidence while still controlling work:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head-100-diagnostic-train `
  --run runtime\reports\gamer-v1\transition-005-expanded-head-100-0001 `
  --expanded-diag-steps 20 `
  --expanded-diag-eval-every 5 `
  --expanded-diag-trials mixed,lr-quarter,zero-focus,game:captain_crew_risk,game:bertrand_oligopoly `
  --device cuda
```

### Optional capacity-matched comparison (the decisive input-information control)

The default diagnostic assesses the **existing trained checkpoint** and its update mechanics. To test whether the 100 sampled vectors add useful learning information **without changing capacity**, include:

```powershell
  --expanded-diag-matched-steps 100
```

This adds two **fresh** 102,400→4,096→2,048→512→128 C0 Wide trials with the same initialized weights, original AdamW loss, batch schedule, and frozen LoRA. One trial receives all 100 sampled contextual vectors; the other receives the final-token vector replicated 100 times. The resulting parameter counts are identical. This control can use substantial additional CPU RAM/GPU time because each trial trains the full 419-million-weight input layer. Use `--expanded-diag-eval-every 20` with 100 matched steps. A short matched run measures early learning, not final convergence; extend only if the measured curves warrant it. `0` (the default) skips this additional training experiment.

Other options:

- `--expanded-diag-train-eval 0`: evaluate **all** training examples at baseline and trial completion (default); a positive limit takes a target-agnostic deterministic subset.
- `--expanded-diag-ablation-cases 128`: sample count for **fixed-weight** input interventions; `0` skips. These are *out-of-distribution sensitivity probes*, not a capacity-matched training test.
- `--expanded-diag-precision-cases 4`: CPU FP32 master versus FP16 GPU mirror on same examples; `0` skips.
- `--expanded-diag-output PATH`: new report directory, outside both source and expanded training runs.

## Evidence and interpretation

`best_model.pt` is an evaluator-only checkpoint of the best weights, **without Adam optimizer state**. `latest.pt` is the *only* checkpoint that permits an exact optimizer continuation. The diagnostic compares both best and latest on all dev and all training cases, including per-game MAE, zero and signed target cohorts, changes in previously correct cases, and prediction-sign collapse. It then forks isolated training trials from `latest.pt` with identical RNG for mixed and quarter-LR controls. Each step logs sampled game IDs and target zones, loss, CPU master parameter changes, GPU FP16 mirror changes, and the fraction of sampled master updates not visible in FP16. Each scheduled evaluation logs all 438 held-out predictions and forgetting transitions.

**Reports** are written in a new sibling `*-diagnostic-train-0001`:

- `final_report.json`: checkpoint comparison, optimizer experiment curves, train/dev metrics, class and game detail, FP16 precision test, ablation sensitivity, optional same-capacity scratch comparison and explicit limitations.
- `provenance.json`: exact frozen source fingerprints, split sizes, checkpoints, trial parameters and capture identities.
- `predictions.csv`: all per-case dev predictions for checkpoint baselines and each controlled evaluation.
- `events.jsonl`: every isolated training update, gradient scale and sampled parameter/FP16-delta telemetry.
- `failure_report.json`: created only when an error prevents completion; original runs remain unchanged.

**Decision logic:** if train error improves while dev worsens, that's evidence of generalization divergence. If lost zero/sign cases cluster by training regime, that's evidence of interference from those updates. If sampled CPU master changes do not survive FP16 conversion, numerical-update resolution is a credible limiting mechanism. If fixed-weight input ablations change outcomes, they show reliance on contextual slots—not that any alternative input scheme would train better. None of these tests by itself proves the 100-vector scheme is defective or absolves the frozen Qwen representation.

## Memory and verification

This diagnostic is intentionally heavyweight. It loads the original dense 100-vector C0, cached features, and CPU Adam moments. For typical CUDA + CPU AdamW backend, expect several GB of system RAM and GPU use. Checkpoints are loaded with memory-mapping rather than eagerly duplicating their contents; the optimizer's moments are reset **in-place** between trials, avoiding repeated large allocations. Full train and dev evaluations may be slow. No adapter, source head, source cache, or source checkpoint is overwritten or promoted. Save/stop the original run first.

`pytest -q tests/test_nanojev_gamer_v1_expanded_diagnostic_train.py tests/test_nanojev_gamer_v1_expanded_head.py`
