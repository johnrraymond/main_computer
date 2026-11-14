# Qwen mathematical frontier: read-only cross-task retention audit

## Purpose

Use `tools.nanojev_qwen_lm_frontier_retention_audit` **without stopping or
modifying** `tools.nanojev_qwen_lm_radius_ladder_frontier`. It copies the
currently committed `working.pt`, immutable `seed_snapshot.pt`, and available
per-task best-reference-loss LoRA snapshots to its **own independent output
folder**. It never creates or edits files in the active training campaign,
loads no optimizer, and never calls an optimizer step.

The default inference device is **CPU** to avoid competing for VRAM with an
ongoing CUDA training process. CPU inference can be slow and can still consume
significant RAM and CPU time. The default `--cpu-threads 2` limits
CPU thread contention with the training process; it does not eliminate
memory or disk contention. Use `--device cuda` only if sufficient GPU
headroom exists and concurrent training contention is acceptable.

## Install and run

The patch is manifest-free: install with the project `new_patch.py`.
The original radius ladder, frontier trainer, and task generators are **all
untouched** by this patch.

```powershell
& $py -m tools.nanojev_qwen_lm_frontier_retention_audit `
    --device cpu `
    --frontier-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1" `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --scope visited `
    --fixed-cases 8 `
    --novel-cases 8
```

No separate snapshot command is needed. By default reports appear under the
sibling directory `qwen_lm_frontier_v1_retention_audits/audit_<timestamp>_<id>/`.
Use `--report-root "C:\...\some_other_folder"` for another **separate** location.

### Interpret each comparison

- `seed -> latest`: **acquired** means originally wrong but now correct;
  **regressed** means originally correct but now wrong; **retained** means
  both correct; **still_wrong** means neither correct. The final net accuracy
  includes regressions: `latest_correct = retained + acquired`.
- `task-best -> latest` (enabled by default): same four categories, but
  comparing *each* visited task to its saved **lowest-reference-loss**
  historical LoRA checkpoint. This is the direct within-campaign retention
  diagnostic; lowest teacher-forced loss does *not* guarantee best greedy
  accuracy at that earlier checkpoint. Missing task-best files are explicitly
  listed, never silently treated as correct.
- `previous -> latest` is optional, via `--compare-previous`: compare the
  previous *committed* `working.pt` (usually just 10 steps old). This is a
  short-window stability check, **not** a substitute for historical retention.
- `fixed` deck uses deterministic task indices and training seed so the *same
  cases* can be compared across audit runs. These indices differ from the
  trainer's normal gradient batches and reference-loss probes.
- `novel` deck uses a fresh, recorded random seed every audit. It samples
  problems from the same task families, so it tests new examples from known
  templates, **not** unbounded mathematical generalization. Some templates
  have limited distinct problems and may repeat an equivalent example.
- `--scope visited` (default) covers all tasks the model has reached,
  including its current task, rather than requiring the whole curriculum to
  finish. `--scope all` measures all configured tasks, including not-yet-
  visited tasks. Those results are exploratory, not retention claims.

## Outputs

Each invocation creates a **new** audit directory:

```text
qwen_lm_frontier_v1_retention_audits/
  audit_<timestamp>_<id>/
    snapshots/
      seed.pt
      latest.pt
      task_best_<task>_L<level>.pt
      previous.pt             # only if --compare-previous
    cases.jsonl              # exact fixed and novel inputs
    results.jsonl            # every actual greedy output and comparison
    report.json              # status, hashes, task/level accuracy, regressions
```

`report.json` includes `summary.all`, `summary.fixed`, `summary.novel`,
`summary.<task>`, `summary.<task>:L<level>`, and
`task_best_to_latest.by_task_level` for historical retention. It reports
both gross corrections and regressions so net accuracy cannot be inflated.
The report records model hashes, checkpoint steps, selected tasks, random
seed, and the numerical-terminal preflight.

The program emits `QWEN_FRONTIER_AUDIT_START`, per-snapshot
`QWEN_FRONTIER_AUDIT_PROGRESS`, `QWEN_FRONTIER_AUDIT_TASK_BEST`, and
`QWEN_FRONTIER_AUDIT_DONE`. If interrupted, the audit's own `report.json`
remains `RUNNING` and a subsequent invocation creates a new report. It never
alters the training run or resumes a partial audit with mixed model weights.

## Important limits

**This is diagnostic, not a training gate or certification.** It tests exact
`PROBLEM: ...` next-packet text with the same greedy decoder and exact-match
comparator used by the frontier trainer. For noninteger, coordinate, unit,
rational, and symbolic outputs, semantically equivalent alternative
representations are not accepted as correct. This diagnostic does **not**
measure full HybridLink recursion, learned terminal detection, exact `Z`
readout, or open-domain mathematical competence.

It makes **no glass edits, no glass-corpus preparation, and no Qwen changes**.
Training may advance after its copied snapshot; the audit's reports explicitly
identify the committed checkpoint actually measured, not the live final state.
