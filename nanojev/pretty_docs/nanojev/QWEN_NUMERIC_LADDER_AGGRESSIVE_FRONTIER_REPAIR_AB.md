# Numeric-terminal Qwen ladder: aggressive frontier, then paired repair

The existing `tools.nanojev_qwen_lm_radius_ladder` gains an **opt-in** `--frontier`
run. Normal stages, certification, retention floors, and existing checkpoints
are not altered. The new mode operates on a frozen **copy** of an existing
`--output/working.pt`, trains the same Qwen q/v LoRA, and writes a *sibling*
workspace `<original_output>_aggressive_frontier_v1` by default.

**This is text/LM-head training.** `--terminal-mode numeric` requires Qwen to
emit `PROBLEM: <integer>` packets. HybridLink and the trained numerical head
are neither executed nor trained. The frontier radii bound signed **results**;
`--operand-limit 1024` independently bounds every operand and intermediate sum.

## Stage 1: aggressive frontier expansion

Use the original ladder command with `--stage run --frontier` and the following
optional knobs. Keep `--output` pointed to the **existing** numeric ladder
workspace; do not point it at the frontier workspace. The existing
`--numeric-bootstrap-from` argument is irrelevant in this mode: the adapter
is copied from the source's `working.pt`, not bootstrapped again.

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage run --frontier --device cuda --terminal-mode numeric `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1" `
    --radii 0,1,2,3,4 --operand-limit 1024 --max-rung 8 `
    --batch-size 4 --eval-batch 8 `
    --frontier-steps 1200 --frontier-check-every 100 `
    --frontier-max-radius 64 --frontier-eval-cases 192 `
    --frontier-harvest-cases 48 --frontier-repair-center-cap 256 `
    --frontier-neighbor-probe-cap 384 --frontier-repair-steps 150 `
    --local-radius 4
```

By default, the sampled signed-result shells are **4, 8, 16, 32, 64**.
Within the shells, large-magnitude operand bands up to 1,024 are deliberately
sampled. The source ladder's `--t00-mode exhaustive`, `--retention-floor 1.0`,
`--advance-mode exact`, `--adaptive-repair`, `--steps-per-rung`, and strict
certification policies are **not** applied in this mode. This intentionally
relaxes retention constraints but records observed losses.

Events: `QWEN_FRONTIER_START`, `QWEN_FRONTIER_CHECK`, `QWEN_FRONTIER_DONE`.
The score compares **identical, fixed evaluation problems** against the
pre-stress baseline and the previous check. Discovery cases are separately
written to `discovered_errors.jsonl` with source, target, Qwen emission, rung,
radius, operands, and discovery step. Both correct and incorrect observations
appear in `observations.jsonl` for longitudinal analysis.

The run finishes after the configured optimizer budget; it does **not** claim
that the frontier or net learning rate has reached a plateau. `best.pt` is the
best observed score on the fixed evaluation; `stress.pt` is the final, possibly
more aggressively adapted, checkpoint from which repair branches start.
Interrupted runs resume only if policy and source-checkpoint hashes match.
Pause other training that might modify the original `working.pt`.

## Stage 2: A/B repairs on the stressed adapter

Use **the same stage-1 options and values**, changing only `--stage run
--frontier` to `--stage repair-ab`. The relevant required arguments are repeated
here so checkpoint-policy validation is exact:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage repair-ab --device cuda --terminal-mode numeric `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1" `
    --radii 0,1,2,3,4 --operand-limit 1024 --max-rung 8 `
    --batch-size 4 --eval-batch 8 `
    --frontier-steps 1200 --frontier-check-every 100 `
    --frontier-max-radius 64 --frontier-eval-cases 192 `
    --frontier-harvest-cases 48 --frontier-repair-center-cap 256 `
    --frontier-neighbor-probe-cap 384 --frontier-repair-steps 150 `
    --local-radius 4
```

Before repair, the runner reads accumulated errors, rechecks them against
`stress.pt` and discards cases that are no longer failures. A and B get the
**same** center failures and are initialized independently from the **same**
stressed LoRA and AdamW optimizer moments.

- **A_direct:** supervised correction using only the actually broken centers.
- **B_neighborhood:** before any branch trains, evaluate adjacent signed
  `(+d,−d)` perturbations of these centers on the stressed model. Train with
  actual failed neighbors, retain correct nearby examples as replay, and
  include the centers. The total signed result and operand/partial bounds are
  preserved. Unprobed neighbors are *not* assumed incorrect.

The two branches use the same **number of optimizer steps**; elapsed training
time and B's local neighborhood discovery cost are reported. Both are scored
against the same held-out, fixed initial-versus-stressed evaluation deck,
which **never supplies repair training examples**. The report separately
measures recovery on the repair centers, and net acquired/lost competence on
held-out cases. In particular, repairing the centers does not automatically
count as out-of-sample frontier generalization.

Events: `QWEN_REPAIR_NEIGHBOR_MAP`, `QWEN_REPAIR_BRANCH_DONE`,
`QWEN_REPAIR_AB_DONE`. Report: `repair_ab_report.json`. Branch checkpoint files
are `A_direct.pt` and `B_neighborhood.pt`. The original numeric ladder is never
written by either stage.

**Locality caveat:** B restricts its *training examples to local arithmetic
input neighborhoods*, not the tensor coordinates modified by gradients. Both
branches still change shared Qwen LoRA weights; this test verifies whether
local-neighborhood repair is better than direct correction. It does **not**
prove sparse/isolated LoRA parameter repair or noninterference.

Status without CUDA/model loading:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder --stage status --frontier `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1"
```

### Validity and stop conditions

- Refuse missing/non-numeric source checkpoints, overwritten parent paths,
  changed source checkpoints or policies on resume, or changed evaluation
  decks. Stage 2 refuses an unfinished `stress.pt` and missing repairs.
- There is no requirement for zero forgetting; gains minus losses is the
  primary objective. Preserve best and final stressed checkpoints separately.
- Neither branch proves an information-rate advantage until independent GPU
  timing and harder fresh-frontier evaluations have been obtained.
- Runtime CUDA behavior still requires testing on the user's GPU.

## Rung-by-rung competency and autonomous complete-chain audit (extension)

The opt-in `--frontier` stage now provides **two different correctness
measurements**, without changing its training sampler, optimizer, schedules,
retention policy, or parent checkpoints.

**One-hop T-rung accuracy:** each `QWEN_FRONTIER_CHECK` contains
`versus_start.by_rung` and `since_last_check.by_rung`, with `T00` through
`T08` independently reporting `tested`, `correct_before`, `correct_now`,
`gained`, `lost`, `net`. These measure the exact one-step packet rewrite
for that rung, NOT end-to-end problem solving. They are computed on the
existing fixed 195-example evaluation deck (about four per rung per radius
in the current default), with the same problem identities at every check.

**Autonomous full chain:** select the T08 members of the original fixed
evaluation deck (20 examples across radii R4–R64 in the standard run).
Starting from their original ten-operand `PROBLEM:` packet, run an *unassisted*
Qwen greedy reduction; give its **actual emitted packet**, not the gold target,
back as its next input. Repeat at most nine reductions (T08 through T00),
stopping at a canonical numerical terminal or upon malformed/nonprogressing
output. The strict trajectory succeeds only if **all nine emissions exactly
match their canonical reference reductions**. Separately score whether the
terminal integer is correct even if an intermediate reduction differed.
This gives two meaningful, distinct metrics: `strict_correct` and
`terminal_correct`. `first_failure_by_rung` identifies the first incorrect
transition (e.g. `T06`) and distinguishes malformed or premature completions.

Output events:

- `QWEN_FRONTIER_CHAIN_BASELINE` — baseline evaluated against the original
  ladder checkpoint (not the already-stressed model).
- `QWEN_FRONTIER_CHAIN_CHECK` — full-chain gains, losses, exact-chain and
  terminal-only counts, first failing rung and per-radius breakdown,
  relative both to baseline and preceding check.
- The existing `QWEN_FRONTIER_CHECK` — now also includes `by_rung`.

Detailed scores, actual emitted strings and intermediate prompts are appended
to `chain_observations.jsonl`, and the frozen complete-chain deck is recorded
in `chain_evaluation_deck.json`. The checkpoint stores the complete-chain
baseline and last observation, so successive checks compare *the same cases*.
The `--stage status --frontier` report includes the latest chain metrics.

**Existing in-progress runs:** install the patch and resume the identical
`--stage run --frontier @frontierArgs` command; no new CLI flags are required.
On first resume from a pre-extension `working.pt`, the runner reconstructs
the **original** chain baseline by temporarily restoring the original parent
LoRA and then restoring the saved stressed LoRA without training either. It
also records the current checkpoint's chain result before resuming optimizer
updates. An already finished `STRESSED` run can be reevaluated by invoking
the same command: there is no new training. The old one-hop history is not
rewritten or misrepresented as earlier full-chain measurements.

The `repair-ab` stage now also reports `chain_from_stressed` and
`chain_from_initial` for A (direct examples) and B (local-neighborhood
examples) at the same shared chain probes. These metrics include both exact
complete-chain acquisition and final-answer acquisition. `evaluation_seconds`
is tracked separately from optimizer time.

**Evidence limitations:** This diagnostic measures only signed addition
reductions with up to ten operands, result radii R4–R64 and magnitudes up
to 1,024. Exact final-integer success is NOT equivalent to exact canonical
step-by-step success. The existing one-hop evaluation deck has known T00
source/target overlap with two training examples; this patch deliberately
preserves the legacy deck and its hash to allow unmodified resume. T08
initial evaluation prompts do not include those T00 duplicates, but their
intermediate packets are not separately certified disjoint from the entire
training corpus. This is a training-progress diagnostic, not independent
held-out proof of generalized arithmetic.

CUDA inference for the chained evaluation is more expensive than one-hop
inference: in the current 195-example evaluation deck it evaluates ~20 T08
chains of up to nine greedy emissions at each check. The default
`--frontier-check-every 100` continues to govern its cadence.
