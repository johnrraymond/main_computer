# Qwen mathematical frontier: independent acquisition cutover

## Purpose

`python -m tools.nanojev_qwen_lm_radius_ladder_frontier --stage run`
continues from a **snapshot** of the active numeric-terminal radius ladder,
without touching `tools/nanojev_qwen_lm_radius_ladder.py`, its training run, or
its exact-certification rules. This is a **Qwen-LoRA acquisition campaign only**.
No glass training, glass-corpus preparation, HybridLink training, detector
retraining, or numerical-Z-head training occurs here.

The governing source is `pretty_docs/nanojev/NUMERICAL_COMPLETION_AND_MATH_FRONTIER_TRAINING_PLAN.md`,
especially the ten-branch dependency/frontier table. The original document's
exact-acquired-memory promotion requirement is *deliberately not a gate on this
Qwen-only acquisition phase*. Exact end-to-end certification remains separate.

## Tasks

`tools/nanojev_qwen_lm_frontier_tasks.py` has one deterministic, exact
transformation generator for each of nine trainable families:

- Signed arithmetic and leftmost reduction chains, starting at the **actual
  source rung** and operand magnitudes around **1024**, sampling on both sides.
- Place value: hundred, thousand, million, billion, trillion, powers of ten.
- Operators: subtraction, multiplication, exact division, modulo, precedence.
- Fractions, percentages, proportions and unit conversions.
- Algebra: linear equations, equality and substitution.
- Logic and graphs: Boolean operators, implication, directed reachability.
- Coordinates and geometry: exact integer displacements and distances.
- Probability: finite probabilities and expectation.
- Symbolic extreme scale: operations on unexpanded powers of ten.

The **tenth** documented foundational branch, exact integer literal/completion,
is checked at startup rather than re-taught as an answer-prefix generation
problem. `PROBLEM: 0` and `PROBLEM: -12` are already terminals; `PROBLEM:
10+(-10)` is not. Only canonical integer outputs carry `terminal_kind=int`.
Rational, unit, coordinate, and symbolic targets are labelled as *one-step
representations* and are **not** claimed to be recognized numeric terminals by
the current integer grammar. Qwen can acquire these representations without
silently altering the detector's accepted language. This patch does not complete
P1–P3 of the broader detector/HybridLink/exact-Z design plan.

Each task family has increasing levels; the initial number of levels is two.
They have finite generators of mathematical transformations, **not** a claim to
cover every mathematical task described in the larger design specification.
Advanced named constants, conditional probability, general graph algorithms,
multivariate algebra, and new terminal types remain future task extensions.

## Training and saturation

- Start at the active signed-arithmetic frontier (`source_rung`, operands near
  1024). No T00/R0 exhaustive pre-scan.
- Take fresh deterministic examples every step and include modest replay from
  already visited tasks (`--replay-per-batch`), without 100% accuracy gating.
- Every `--check-every` steps, monitor average teacher-forced LM loss on a
  stable seeded reference set *within that task*, smooth with `--loss-window`,
  and advance on nonimprovement for `--plateau-patience` checks after the
  minimum training interval. **This is task-specific loss stability, not exact
  mathematical correctness nor a proof of generalization.**
- A configured `--max-steps-per-task` **stops without advancement** if the
  task has not plateaued. Default 0 means no arbitrary per-task limit.
  `--max-total-steps` can pause an overall run; increase it to resume.
- During those same checks, perform a small number of genuine unassisted greedy
  generations and immediately append failures to `discovered_errors.jsonl`.
  Never run a long preliminary failure census. All failures remain historical
  observations even if subsequent training later fixes them; **no glass-corpus
  preparation or resolution triage takes place here**.
- Save `working.pt` (LoRA, optimizer, RNG, task cursor, plateau state),
  `seed_snapshot.pt` (immutable import), `final.pt` after all tasks plateau,
  best *per task* LoRA checkpoints, `frontier_progress.json`,
  `frontier_history.jsonl`, and append-only error observations.

### Install and use

The patch is **manifest-free**. Install using the project's `new_patch.py`, then:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --seed-working "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1\working.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

This is **one streaming training command**. Running it again with identical
training policy resumes from the local `working.pt`, not from the live old
ladder checkpoint (which may have advanced meanwhile). Adjusting the
operational total-step and per-task limits for a resumed run is supported.
The first model load still requires the original Stage1/text parent files,
whose lineage is checked against the seed.

For a no-model curriculum preview:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier --stage preview
```

For a zero-completion check without loading models:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier --stage detect --packet "PROBLEM: 0"
```

For status without loading the model:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier --stage status `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

Output prefixes: `QWEN_FRONTIER_SEED`, `QWEN_FRONTIER_START`,
`QWEN_FRONTIER_TRAIN`, `QWEN_FRONTIER_CHECK`, `QWEN_FRONTIER_ADVANCE`,
`QWEN_FRONTIER_STOP`, `QWEN_FRONTIER_DONE`.

## Limitations and provenance

The verified CPU tests use fake tiny LoRA/module fixtures and test the actual
controller, generator, snapshot, logging, and resume semantics. **They do not
constitute a Qwen CUDA training result or a learned mathematical frontier.**
Greedy errors are anchored to the seed SHA-256, exact task/seed/index, and the
training step at which they were observed. Historical observations are not
re-certified against the final model. The trainer does not promise to keep
an immutable full-model snapshot at *every* historical error step; the records
say `in_memory_training_step_N` to avoid implying that they do.

Using a new training task may require extending the numeric-terminal grammar,
detector, or readout later; **this trainer does not silently broaden numerical
completion beyond canonical integers**.

## Hotfix: low-loss plateau detection and restartable training snapshots

This update modifies **only** the frontier trainer and its tests/docs. The old
`tools.nanojev_qwen_lm_radius_ladder.py` is unchanged, and no glass code is
modified. Install the hotfix over the already installed frontier cutover; it
**resumes the same** `qwen_lm_frontier_v1` campaign. Do not change the output
folder or recopy the seed.

### Correctly detect improvements at small loss

The initial plateau logic demanded an improvement larger than **both** an
absolute `--plateau-delta 0.002` and a relative threshold. As reference loss
approached 0.001 this could treat an enormous relative reduction as no progress.
The corrected logic accepts a meaningful **absolute or relative** reduction,
using `min(absolute_delta, abs(best_smoothed_loss) * relative_delta)` as the
threshold. The same CLI values are retained for in-flight campaigns, and an
old checkpoint's false accumulated patience is reset once on migration.
Reference loss is still the task-specific, seeded teacher-forced metric; this
is not a correctness certificate or end-to-end termination test. The task best
LoRA file now tracks the lowest **raw** reference loss separately from the
smoothed plateau anchor.

### Snapshots and recovery

- `working.pt` is the **authoritative** complete resume state: LoRA weights,
  AdamW optimizer, Python/PyTorch/CUDA RNG states, task/index cursor, plateau
  window, and pending-check marker. `frontier_progress.json` is informational.
- Save after every **10 completed optimizer steps** by default, and after the
  first completed step past **60 seconds** without a save. Change operational
  cadence with `--save-every N` and `--save-seconds S`, without changing the
  mathematical curriculum or invalidating the saved run's training policy.
- Each committed snapshot writes and syncs a temporary file before atomically
  replacing `working.pt`. A `previous.pt` copy holds the previous complete
  snapshot. If `working.pt` cannot be deserialized, the trainer preserves it as
  `working.corrupt.pt` and restores the valid `previous.pt` automatically.
- Before slower scheduled loss/greedy checks, the trainer saves the completed
  training step with `pending_check=true`. After restarting, that check runs
  before new optimization. A partial error log is reconciled without appending
  duplicate failure events or double-counting those errors.
- Ctrl+C or SIGTERM requests a **graceful stop** at the next completed step,
  saving `working.pt` with status `PAUSED`. Re-run the *same* command to resume.
  `--stage status --output ...` reads `working.pt` rather than possibly stale
  `frontier_progress.json`.
- Abrupt power loss or process termination without a catchable signal can still
  lose updates made since the last committed snapshot; it cannot guarantee
  zero loss of work. Use `--save-every 1` to prioritize recovery precision at
  the cost of frequent disk writes. `previous.pt` protects against one damaged
  latest checkpoint, not multi-file filesystem failure.
- **Existing running Python processes do not pick up source-code changes.** To
  activate the new stop handler and frequency, restart the frontier training
  process. The pre-hotfix version cannot retroactively save unsaved steps.

### Run/resume commands

Keep the original cutover command (same `--seed-working` and `--output`).
Optional explicit safety cadence:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --seed-working "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1\working.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1" `
    --save-every 10 --save-seconds 60
```

For latest committed step without model initialization:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --stage status `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```
