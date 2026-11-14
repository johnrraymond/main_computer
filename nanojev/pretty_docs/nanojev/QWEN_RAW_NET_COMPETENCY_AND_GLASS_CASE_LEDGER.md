# Raw Qwen net competency measurement and glass case archive

**Objective:** measure raw-Qwen mathematical competency acquisition **minus** forgetting, on *identical problems over time*, and save every observed hard case for later glass training. This is not a comparison of training hours or prediction loss.

## On every raw-training check

Both `--phase systematic` and `--phase samples` now emit `QWEN_NET_COMPETENCY`, containing:

- `paired.gained`: identical problems **wrong at an earlier check and correct now**.
- `paired.lost`: identical problems **correct earlier and wrong now**.
- `paired.net`: gained minus lost, with `net_per_100_steps` as a sampled rate (not an exhaustive frontier-wide rate).
- `fixed_anchor_paired`, `paired_by_cell`, `paired_by_band`: where the changes occurred.
- `recent`: gains, losses, and net from up to four recent paired checks.
- `wrong_current`, `by_task_band`, `hard_archive_size` and `replay_archive_size`.

An untested new case does **not** count as a gained competency just because Qwen happened to get it right. The first observation establishes that problem's baseline. This avoids misleading net gains when the test set changes.

Checks interleave with training, with no large upfront scan. An immutable anchor set covers every configured task/level; a small deterministic fresh frontier set probes the current boundary and nearby bands; an archived subset is re-evaluated. Only signed arithmetic and arithmetic operators have actual operand-radius scaling; the other seven mathematical classes use their native task scales. By default: one fixed anchor per cell, 18 fresh probes, and up to 24 archived replay problems per check. Override with `--competency-anchors`, `--competency-fresh`, `--competency-replay`. These affect observation workload, not the optimizer's pinned policy.

## Stopping

The existing loss convergence calculation is preserved. However, **loss convergence alone no longer authorizes advancement or stopping**: the recent four paired competency checks must show nonpositive net growth. A positive net means that raw Qwen is still learning more on the sampled set than it loses, even if loss is flat. This is a practical sampling-based stop criterion, not proof of complete frontier saturation; sample variance and finite coverage remain.

## Raw failures preserved for glasses

Per run, in the raw output directory:

- `competency_checks.jsonl`: resumable, exact per-problem correctness by training step and paired competency diagnostics.
- `glass_hard_candidates.jsonl`: deduplicated prompt/answer failures with task/level, originating phase, observed step, generator settings and band where applicable. It also imports previously detected raw frontier failures; those are labeled **historical_unrechecked**, not claimed to remain wrong.
- `glass_replay_candidates.jsonl`: deduplicated successes to help detect glass regressions.

An interrupted check can replay its already-completed record at the same training step; future journal entries are excluded on checkpoint rewind. This does not change the saved backbone, LoRA, optimizer, or training RNG.

**Existing completed raw runs:** no instrumentation can reconstruct historical net transitions that were never measured. Re-running a Phase 2 command against `status=PLATEAU` is idempotent; it does not reopen training. Instrumentation becomes active in a future raw acquisition run. Existing recorded errors *can* be archived right now without running inference:

```powershell
& $py -m tools.nanojev_qwen_lm_competency_ledger `
  --systematic-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1" `
  --samples-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_v1" `
  --diagnostic-report "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_v1_error_maps\map_20261030T235339_045812Z_16cc78"
```

The import is idempotent and reads the existing 90-case diagnostic hard archive plus original systematic and mixed discovery files. It does **not** claim that all historical errors remain incorrect on the selected frozen checkpoint. The glass runner needs a separate candidate-loading change to consume this archive; this patch exports the complete cases but does not modify the already-running Easy/Mixed/Hard glasses or their checkpoints. The raw-to-glass handoff must recheck imported problems against the *selected* frozen backbone once, not rescan the frontier to rediscover them.

## Acceptance checks

1. During the next raw run, verify `QWEN_NET_COMPETENCY` at each scheduled evaluation; distinguish `gained`, `lost`, and `net`.
2. Check that `competency_checks.jsonl` contains consistent paired observations after an interruption/resume and does not count unseen new problems as gains.
3. Check `glass_hard_candidates.jsonl` contains real prompts/targets, includes errors from both raw passes, and does not duplicate known cases after restart.
4. Verify when robust loss says converged, raw training only stops when the net-growth gate is also satisfied.
5. Glass acceptance must independently track net corrections and regressions on a common evolving reference set and compare against continued raw Qwen from the same checkpoint.
