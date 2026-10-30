# Qwen radius ladder: numeric-terminal acquisition contract

## Intent

A complete numerical problem is **already the answer**. No generated `ANSWER:` transition is needed. The new CLI default for `tools.nanojev_qwen_lm_radius_ladder` is `--terminal-mode numeric`.

- T00 one-hop target: `PROBLEM: 6+(-6)` -> `PROBLEM: 0`.
- T01+ one-hop target: `PROBLEM: (6+(-6))+3` -> `PROBLEM: 0+3`, then later -> `PROBLEM: 3`.
- `numerical_completion('PROBLEM: 0')` returns `{"numerical": true, "complete": true, "value": 0}`.
- `PROBLEM: 10+(-10)` and `PROBLEM: 10 trillion` are **not** terminals. Detection requires a single canonical integer literal occupying the whole `PROBLEM:` payload.
- No gold target, output-head logits, or answer prefix are inputs to the detector. The oracle remains confined to supervised training and offline grading.
- No external HybridLink/detector model is run by this ladder. This is a strict *packet-level completion signal* for the future HybridLink termination interface, not a claim that a learned detector has already been integrated.

## Why new checkpoints are required

The old certified T00 weights were trained to produce `ANSWER: n`; their certificates cannot validate `PROBLEM: n`. The config hash records the new mode and the resume path forbids changing terminal contracts inside an existing output directory.

Keep all old runs and their checkpoints. Use a *new output directory* for numeric mode. The optional `--numeric-bootstrap-from` imports **only** LoRA weights from an earlier immutable `certified_T00_R0000.pt` in the supplied campaign folder, after checking parent lineage and original certificate; it resets old certification and optimizer-step accounting in the new campaign and copies the seed locally. It does not touch the parent.

`--fork-certified-zero-from` and `--warm-start-ladder` remain **legacy-only**. For a legacy resume, explicitly provide `--terminal-mode legacy` and retain the old run's flags.

## Quick zero-dependency detector verification

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder --stage detect --packet 'PROBLEM: 0'
& $py -m tools.nanojev_qwen_lm_radius_ladder --stage detect --packet 'PROBLEM: 10+(-10)'
```

## Fresh numerical acquisition run using the old certified LoRA seed

Re-use the same Stage 1 and text checkpoint paths you supplied before, but replace the **old fork argument** and **old output path**, for example:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
  --stage run --device cuda --terminal-mode numeric `
  --stage1-checkpoint 'C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt' `
  --text-checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --radii 0,1,2,3,4 --operand-limit 1024 --max-rung 8 `
  --t00-mode exhaustive --per-cell 8 `
  --steps-per-rung 10000 --repair-steps 25 --batch-size 4 --replay-per-batch 1 `
  --eval-batch 8 --fail-limit 64 --probe-cases 128 --audit-progress-every 128 `
  --numeric-bootstrap-from 'C:\Users\subsi\NanoJev\runs\qwen_lm_exact_radius_v1' `
  --output 'C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1' `
  --retention-floor 1.0 --local-radius 4 --local-repair-steps 10 `
  --advance-mode exact --advance-accuracy 1.0 `
  --retention-sample-cases 128 --retention-known-failure-cases 32 `
  --retention-full-every 10 --gate-full-every 10 `
  --adaptive-repair --adaptive-check-min 5 --adaptive-check-max 100 `
  --adaptive-repair-min 2 --adaptive-repair-max 20 `
  --adaptive-harvest-cases 128 --adaptive-failure-target 8 `
  --adaptive-full-steps 1000 --curriculum rung-major
```

**Exact-mode audits are expensive.** `--retention-floor 1.0` requires zero observed historical errors on each audited retention deck. Note that routine retention may still be sampled; full exact certification of all acquired history requires complete retention audits. This command does not add any glasses or retrain the separate HybridLink learned detector.

## Files

- `tools/nanojev_qwen_lm_radius_ladder.py`: new terminal contract, detector, safe seed/bootstrap, audit metrics.
- `tests/test_nanojev_qwen_lm_radius_ladder_numeric.py`: syntax, terminal/computation distinction, lineage and bootstrap safety.

Tested locally on CPU; no CUDA or user checkpoint was available for a live run.
