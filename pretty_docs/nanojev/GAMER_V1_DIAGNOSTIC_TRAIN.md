# GAMER V1: isolated diagnostic training

The diagnostic-train phase is an explicit, potentially expensive investigation of forgetting across LoRA representations, C0 Wide compression, numerical losses, and game/table sampling. It is not the normal trainer, does not promote weights, and does not write to the source run.

## Command

```powershell
& $py -m tools.nanojev_gamer_v1_staircase diagnostic-train `
  --run runtime\reports\gamer-v1\transition-005 `
  --device cuda
```

The default is the **entire certified training and development corpus**, two matched seeds per trial, a private 12-step LoRA continuation, 120 head steps per matched experiment, 90 steps per interference-sequence experiment, and an evaluation every 10 updates. This can be expensive, especially the LoRA clone training and fresh Qwen feature captures. It is deliberately opt-in.

Useful budget controls:

```
--diag-lora-steps 12          # Additional LoRA optimizer steps on clone; 0 skips with INCONCLUSIVE result
--diag-head-steps 120        # Head training updates per controlled experiment
--diag-sequence-steps 90     # Game/table/zero-line scheduling interventions
--diag-eval-every 10         # Frequency of full train+dev per-case evaluation
--diag-seeds 2               # Matched random initializations and batches
--diag-max-games 0          # 0 = all certified games
--diag-train-per-game 0     # 0 = all training cases for selected games
--diag-dev-per-game 0       # 0 = all development cases for selected games
--diag-game-limit 12        # Number of individually isolated high-error games; 0 = all
--diag-radius 1.0           # Exact-zero/small signed numerical region
--diag-capture-batch 2      # Qwen capture batch size
--diag-checkpoint-every 0   # 0 = start/end per experiment; N = extra periodic snapshots
--diag-output <new-path>    # Must be outside the active source run; never overwrite
```

## What is actually tested

- Verifies corpus SHA, prompt SHA, LoRA SHA, C0 Wide head SHA, exact cached-feature lineage, and original training/development split. Reuses verified cached frozen-step-1800 features.
- Compares original best LoRA features, current frozen latest LoRA features, and a candidate trained using the **existing LoRA trainer in a copied run**. Each feature set trains an identically initialized new head using identical training-example schedules and seeds. Any gain from a short LoRA continuation is **bounded evidence**, not proof that the LoRA is the root cause.
- Trains the existing 32-dimensional C0 Wide bottleneck, a 128-dimensional alternative, and a diagnostic network whose corrected readout bypasses the final narrowing stage. Wider variants also change capacity; improvements are not automatically assigned to the bottleneck alone.
- Compares the existing signed-log Huber objective with signed-log MSE and raw numeric MAE, recording **both** training objective and raw numerical error throughout.
- Tests mixed sampling, alternating near-zero regimes, blocked near-zero regimes, blocked mutually exclusive table signatures, and a bounded number of individual games. Table membership overlaps and is not treated as four independent categories.
- Measures numerical-region gradient cosines at the saved C0 Wide starting point. The isolated experiments do not alter the active LoRA, numerical weights, optimizer state, or normal CLI behavior.

## Evidence

Each invocation creates a NEW sibling directory, e.g. `transition-005-diagnostic-train-0001`:

| File | Content |
|---|---|
| `provenance.json` | All hyperparameters, dataset and model hashes, certified game coverage, source checkpoint identification |
| `events.jsonl` | Timestamped, immediately flushed step events with exact sampled training indices, games, table signatures, loss, layer-gradient norms, and evaluation metrics |
| `predictions.csv` | Every selected train and development case at each evaluation, targets, predictions, numerical regime, and correctness |
| `groups.csv` | Per game, table signature, and numerical zone at each evaluation; signed-log and raw metrics |
| `forgetting.jsonl` | A previously correct case turning incorrect, with before/after predictions, last observed correct step, first observed failed step, and preceding training game IDs |
| `checkpoints/` | Experiment-local initial, final, and optionally intermediate weights+optimizer states |
| `features/` | Original-prompt-only features for independently captured LoRA candidates, including fingerprints |
| `lora_work/` and `lora_candidate/` | Isolated, resumable LoRA candidate work and frozen adapter snapshot; never replace the source run |
| `lora_extension_stdout.log` | Full stream from real cloned LoRA trainer |
| `partial_summary.json` | Results committed after each completed trial |
| `final_report.json` | Structured hypothesis evidence, per-seed comparisons, caveats, and INCONCLUSIVE when appropriate |
| `failure_report.json` | Fatal stage, exception, traceback, and completed trial results if interrupted or failed |

The report explicitly distinguishes a regression **first observed** at evaluation step N from the exact optimizer update that caused it. The detailed source batches for every intervening update remain available in `events.jsonl`. Train cases alone supply gradients; development cases are never used as training targets.

The analysis does not claim that a short LoRA continuation proves more LoRA training is unnecessary, or that a wider head proves the existing bottleneck has irreversibly lost upstream information. Only promote architectural or training changes after supported causal comparisons.
