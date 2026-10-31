# Qwen frontier error concentration diagnostic

Read-only diagnostic of the completed Phase 2 (`glass_ready.pt`) backbone.
This does not train a glass, modify a checkpoint, or classify entire task
categories as easy at an arbitrary 75% threshold. It produces an empirical
map of *per-problem* failures, high-loss correct answers, and retention tests.

## Run on Windows PowerShell

Install the manifest-free ZIP with `python new_patch.py <downloaded-zip>`.
Then run:

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_error_map `
    --device cuda --cases-per-band 12 `
    --systematic-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1" `
    --samples-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_v1" `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

To compare the current saved best glass (A, B, or both) to the backbone on
**exactly the same diagnostic problems**, add `--glass-stage easy --branches both`.
Glass selection defaults to the sibling `qwen_lm_frontier_mixed_glasses_v1`
workspace. Optional `--glasses-output` can point elsewhere. The best glass is
copied into the independent report directory before inference. This can run
while a separate training process writes new best checkpoints, provided GPU
resources permit; for reliability, run it without competing training.

The tool emits a `QWEN_FRONTIER_ERROR_MAP_DONE` event with the new report path.
The output directory defaults to the sibling
`qwen_lm_frontier_mixed_v1_error_maps/map_<timestamp>_<random>/`, separate
from all training state. Results are appended durably so an interrupted audit
can resume with the *same* command plus `--resume-report <path>`. Do not change
input arguments between runs.

## What is really being swept

Only **signed_arithmetic** and **arithmetic_operators** have an operand
magnitude controlled by the original generator's `operand_boundary`.
For them, each configured level is checked *edge first*, alternating inward
and outward: 1.0, 0.9375, 1.0625, 0.875, 1.125 times its 1024 (L0) or 2048
(L1) nominal boundary. These are nominal bands: generated operands have their
own generator jitter. The report captures measured operand magnitude as well
as target band. Crucially, these anchors are *operand magnitude*, not the
historical signed-result R radius.

Other seven classes use their own native task/level difficulty generators.
The numeric operand boundary does NOT scale those tasks: their results are
reported by `task:L0/L1`, native submode, and as `native`, without pretending
to locate a physical radius. Finite classes such as Boolean/graph logic are
deduplicated by actual source+target string, so counts can be less than the
requested cases. The output always reports its actual denominator.

Historical recorded errors from Phase 1 and Phase 2 are also rechecked,
when the corresponding `discovered_errors.jsonl` files exist. Previously
recorded errors may no longer fail; their current outcomes are evaluated on
the frozen Phase 2 backbone. Disable this replay with `--no-history`.

## Report artifacts

- `summary.json`: baseline greedy accuracy and teacher-forced loss by class,
  level, band and generator submode; worst groups ranked by number of *current*
  errors then mean loss; rising-radius error rolloff (including competent
  inner bands); optional paired glass fixes/regressions.
- `frontier_heatmap.csv`: compact spreadsheet-friendly task-level-band table.
- `results.jsonl`: one record per problem with exact source/target, backbone
  greedy output, correctness, independent teacher-forced loss, optional glass
  A/B greedy output, and paired transition.
- `hard_candidates.jsonl`: baseline wrong problems (actual greedy failures).
- `fragile_candidates.jsonl`: baseline correct but loss ≥ 0.1 by default
  (flag for investigation, **not** a wrong answer). Change with
  `--fragile-loss` as desired.
- `retention_replay.jsonl`: baseline correct, low-loss problems for retention.
- `manifest.json`: frozen source hash, diagnostic policy, progress status and
  checked glass checkpoint hash(es).

These are **diagnostic exports**, not automatically fed into the existing
Glass Easy/Mixed/Hard trainer. A separate, explicit trainer integration should
load and verify these case records, train on real failures, and use independent
paired assessment to prevent regressions. A correct high-loss answer is never
silently relabeled as an incorrect answer.

All metrics are finite-sample diagnostic evidence, not mathematical mastery
certificates or proof of generalization. Reports are immutable with respect to
model/checkpoint sources; only the new report directory is written.
