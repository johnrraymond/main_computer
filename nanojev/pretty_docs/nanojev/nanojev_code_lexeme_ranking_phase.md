# NanoJev binary lexeme training: phase 2 paired ranking

Phase 2 keeps the frozen Qwen backbone and the same binary next-lexeme recognition task. It adds a small paired margin loss using the matched TRUE/FALSE records that already share a code prefix.

For each sampled family:

```text
same prefix P
real lexeme R   -> TRUE
false lexeme W  -> FALSE
```

The existing BCE objective remains intact. The additional ranking score is Boolean log-odds:

```text
score(P, suffix) = true_logit - false_logit
```

The optimizer minimizes:

```text
BCE + ranking_weight * max(0, ranking_margin - (score(P,R) - score(P,W)))
```

Defaults:

```text
ranking_weight = 0.25
ranking_margin = 0.10
```

This deliberately makes ranking an auxiliary pressure rather than replacing the recognition task.

## Safe continuation of the existing experiment

Do **not** reinitialize `main_computer_code_lexeme_v1`.

On the first launch after applying this patch, the trainer branches the head, optimizer, and RNG from the retained checkpoint selected by the existing discriminator policy:

```text
1. highest dev AUC
2. highest dev probability separation
3. lowest dev NLL
```

Cycle numbering, global telemetry, and the source cursor continue forward. The state records the branch point in:

```text
training_objective
ranking_origin_cycle
ranking_origin_dev_auc
ranking_origin_dev_probability_separation
ranking_origin_generation
```

Until the first phase-2 cycle commits, `latest_generation` is pointed at that branch checkpoint, so an interruption restarts from the same known-good discriminator.

Start/restart normally:

```powershell
.\tools\run_nanojev_code_training.ps1
```

The first launch should emit:

```text
ranking_phase_prepare
ranking_phase_best_head_load_start
ranking_phase_started
```

No initializer is required.

## New telemetry

Every training step adds:

```text
pair_rank_loss
mean_pair_logodds_gap
pair_margin_satisfied_rate
```

Every dev evaluation now also measures the exact matched-prefix problem trained by phase 2:

```text
pair_win_rate
mean_pair_logodds_gap
pair_margin_satisfied_rate
```

`pair_win_rate` is the fraction of dev families where the real lexeme's Boolean log-odds score is strictly greater than its matched false lexeme. This is intentionally distinct from global AUC, which compares positives and negatives across unrelated prefixes.

Every cycle result adds:

```text
train_pair_rank_loss
train_mean_pair_logodds_gap
train_pair_margin_satisfied_rate
dev_pair_count
dev_pair_win_rate
dev_mean_pair_logodds_gap
dev_pair_margin_satisfied_rate
delta_dev_pair_win_rate
delta_dev_mean_pair_logodds_gap
delta_dev_pair_margin_satisfied_rate
ranking_weight
ranking_margin
```

Desired direction:

```text
pair_rank_loss                 down
mean_pair_logodds_gap          up
pair_margin_satisfied_rate     up
dev_auc                        up
dev_probability_separation     up
dev_pair_win_rate              up
dev_mean_pair_logodds_gap      up
dev_pair_margin_satisfied_rate up
```

The frozen-backbone invariants remain unchanged:

```text
body_grad_norm = 0.0
backbone_probe_changed = false
head_probe_changed = true
```

## Rolling dev ruler

The original 64 matched dev pairs remain immutable and continue to be evaluated every cycle. They remain the stable longitudinal ruler and retain the established AUC-first checkpoint-selection semantics.

A second rolling dev view is built for every cycle:

- start from the same immutable 64 canonical dev pairs;
- select exactly 32 canonical pairs with a deterministic cycle-derived random seed;
- temporarily replace the other 32 slots with 32 newly sampled matched pairs;
- draw fresh pairs only from the dev split and only from source files not represented in the canonical probe;
- discard the temporary mixture after evaluation and return to the immutable canonical 64 for the next cycle.

This produces a 64-pair stochastic ruler with 50% canonical continuity and 50% fresh held-out evidence per cycle. Replacement is independent of model correctness, so difficult cases are not preferentially removed.

The trainer emits `rolling_dev_ready` with the seed, counts, source-file count, fresh-probe path, and SHA-256. `cycle_result` records the rolling AUC, NLL, probability separation, pair win rate, mean pair log-odds gap, and margin-satisfied rate under `rolling_dev_*` fields. The existing `dev_*` fields remain the immutable 64-pair metrics used for checkpoint selection.
