# Qwen raw vs Glass marginal net-competency rate diagnostic

This experiment is an **independent calibration**. It does **not** declare raw Qwen accuracy-balanced, modify the live Accuracy Balance workspace, or automatically start the production Glass trainer.

## Scientific objective

Find out whether continuing raw Qwen LoRA training or training a frozen-backbone residual Glass A (ungated) or Glass B (gated) yields more **net greedy-answer competency per synchronized training wall second on the same GPU**.

For each branch, on the *same* fixed problem IDs:

- `gained`: previously incorrect -> correct;
- `lost`: previously correct -> incorrect;
- `net = gained - lost`;
- `net_per_gpu_hour_reserved = 3600 * net / synchronized_training_wall_seconds`.

This is an **empirical correctness-transition rate**, not Shannon information in bits and not a global estimate of mathematical knowledge. Report `fresh` and `fresh_expanded` rates separately from the selected `archive_eval` cohort. `all` combines them but is not representative of natural task frequencies.

## Execution

The default starting point is the **best** saved Accuracy Balance checkpoint at
`C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_balance_v1\best.pt`.
It is copied into the diagnostic directory at startup, keeping the live Accuracy Balance **weights and lineage independent**. **Pause the live raw-Qwen training job while running the timed calibration on the same GPU**; concurrent GPU workloads would contaminate the speed comparison. Resume raw training afterward. A different source can be pinned with `--source-checkpoint` (including its `working.pt` after a stable save).

From `main_computer`, with the same `$py` Python environment used for Qwen training:

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_rate_diagnostic `
    --stage preview `
    --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

If the preview shows the expected checkpoint and cohort, repeat the command with `--stage run`. The default experiment spends **120 synchronized training seconds per branch**, using a 40-second evaluation cadence. The GPU should be otherwise idle during each branch. Evaluation and one-time baseline scans are *not* part of the timed training budget; total elapsed process time is therefore longer than six minutes. Change `--budget-seconds 300` and `--check-seconds 60` for a more stable measurement; use `--max-steps` as an optional ceiling (0 means unlimited). The Glass training loop uses the existing rank-one correction trainer, not the old 75%-per-cell Easy/Hard classification.

When completed, examine the report without training:

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_rate_diagnostic `
    --stage report `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

## Outputs and recovery

Under `C:\Users\subsi\NanoJev\runs\qwen_lm_rate_comparison_v1`:

- `source_snapshot.pt`: immutable copied raw-Qwen source checkpoint;
- `manifest.json`, `train_deck.jsonl`, `evaluation_deck.jsonl`: provenance and exact experiment cohorts;
- `train_baseline.jsonl`, `evaluation_baseline.jsonl`: incrementally cached greedy baseline observations;
- `raw_curve.jsonl`, `glass_A_curve.jsonl`, `glass_B_curve.jsonl`: paired acquisition/loss trajectory at training-time checks;
- `raw_working.pt`, `glass_A_working.pt`, `glass_B_working.pt`: resumable optimizer and adapter snapshots after each completed check;
- `raw_result.json`, `glass_A_result.json`, `glass_B_result.json`: branch final measurement;
- `raw_final_lora.pt`, `glass_A_final.pt`, `glass_B_final.pt`: trained diagnostic adapters;
- `report.json`: comparative result, including fresh-probe rate ratios when defined.

Repeating the **same command with the same arguments** reuses the pinned snapshot, baseline cache and completed branches, or resumes a partially completed branch from its latest evaluation. Source and policy drift are rejected. The source checkpoint and the live Accuracy Balance archive are never overwritten.

## Comparison protocol and limitations

1. Every branch starts from the **identical** raw-Qwen LoRA checkpoint. Raw inherits its source AdamW optimizer state; A and B receive independent newly initialized optimizers and zero-residual Glass weights.
2. All three branches consume the same deterministic train-pair sequence. Their step counts differ because the time budget, rather than optimizer-step count, is matched. Raw uses the same autocast policy as the Accuracy Balance loop. Each Glass uses the existing token-level residual training routine (including the expensive inference it needs to locate an error); that expense is included in Glass training time.
3. The evaluation cohort is separately generated or set aside from archive records and does **not** feed training-pair selection. Repeated evaluations use identical problem IDs. This distinguishes actual transitions from freshly sampled accuracy.
4. The archived-error stratum is deliberately hard-case-enriched. The **fresh** original and expanded frontier strata provide a separate measure of generalization; the experiment does not prove all performance on unseen harder problems, especially with a small fixed sample.
5. The synchronized time measures wall time occupied by the sequential training work on the same GPU, including CPU overhead and device synchronization. It is not a hardware counter for GPU active cycles, FLOPs, or energy. Evaluations and baseline inference are reported separately from the timed optimization budget.
6. If a manually configured step ceiling ends any branch before its time budget, the report records `INCOMPLETE_TIME_BUDGET_DO_NOT_CLAIM_CROSSOVER`. Otherwise it says `CALIBRATION_ONLY_REPLICATE_AT_MULTIPLE_RAW_CHECKPOINTS`. A single run does not automatically establish a sustained crossover or change production stopping policy.

**Decision principle:** repeat this diagnostic from successive best raw-Qwen Accuracy Balance checkpoints, using independent fixed-seed probe decks and equal compute budgets. Switch the production training emphasis to Glass when fresh-frontier *marginal net-correct acquisitions per training second* become consistently higher for Glass than raw Qwen. Positive raw gains alone are not a reason to exclude Glass training, and a loss plateau alone is not grounds to start it.
