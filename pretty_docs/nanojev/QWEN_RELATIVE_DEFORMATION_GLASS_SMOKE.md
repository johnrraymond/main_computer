# Qwen Relative Deformation Glass — Two-Pass Smoke

## Goal

Test whether the *residual error mode*, keyed by Qwen's neuronal activation, is
cheaper to memorize than training the Qwen backbone again and again.

**Pass 1** visits the **entire finite selected acquisition corpus** once per
configured epoch, updating only the existing Qwen attention LoRA. Save and
freeze this pass as `baseline.pt`.

**Pass 2** visits the same corpus with the identical saved Qwen LoRA frozen.
A small zero-initialized, activation-localized additive correction is attached
*immediately before Qwen's existing LM head*. **Only the glass is optimized.**
The original ladder `working.pt` is never modified. At first launch we also
copy that exact file to an immutable `source_snapshot.pt` in the new output
directory. Later stages use the snapshot even if the live ladder continues.

The glass is initialized at mathematical zero, not merely approximately zero:

```
h = Qwen's original final decoder hidden state
mode = nearest learned projected activation, if similarity >= threshold
D(h) = learned additive vector for that mode, otherwise exactly zero
logits = frozen_Qwen_LM_head(h + D(h))
```

The fixed seeded random projection (default 48 dimensions) is used only to find
activation modes. The correction codebook (default at most 128 by hidden size)
starts at exactly zero. Unmatched activations always receive zero correction.
There is no independent answer-producing model and no arithmetic executor.

## Apply

The patch contains **three new files** and does not replace the existing
`nanojev_qwen_lm_radius_ladder.py` or your running checkpoints.

```powershell
python new_patch.py "C:\Users\subsi\Downloads\nanojev_qwen_relative_deformation_glass_two_pass_smoke_patch.zip"
```

## Running the full selected corpus

Run from `C:\Users\subsi\main_computer` using the Python interpreter in your
`.venv-diagram` environment.

```powershell
$common = @(
    '--device', 'cuda',
    '--stage1-checkpoint', 'C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt',
    '--text-checkpoint', 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt',
    '--ladder-working', 'C:\Users\subsi\NanoJev\runs\qwen_lm_zero_line_fork_v1\working.pt',
    '--output', 'C:\Users\subsi\NanoJev\runs\qwen_deformation_glass_smoke_v1',
    '--radii', '0,1,2,3,4',
    '--max-rung', '8',
    '--operand-limit', '1024',
    '--per-cell', '8',
    '--t00-mode', 'exhaustive',
    '--backbone-epochs', '1',
    '--glass-epochs', '1',
    '--glass-modes', '128',
    '--glass-threshold', '0.995',
    '--checkpoint-every', '100'
)

# Pass 1: train original Qwen attention LoRA on the complete corpus.
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage backbone @common

# Inspect baseline.pt and report.json before authorizing the cutover.
# Pass 2: reload and FREEZE that backbone, train only the glass.
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage glass @common

# See existing results without loading the model:
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage status `
    --output 'C:\Users\subsi\NanoJev\runs\qwen_deformation_glass_smoke_v1'
```

**The default stage is `backbone`, deliberately requiring a second command for
the glass cutover.** For a single-command controlled smoke, use `--stage both`.
A completed `baseline.pt` or `glass.pt` is immutable and reused on subsequent
runs. Interrupted phases use `backbone_partial.pt` or `glass_partial.pt` with
optimizer state and replay position; the phase resumes instead of restarting.

The **entire corpus** includes each residual `delta` between `-4` and `+4` at
rungs T00–T08; the largest selected radius already includes the smaller nested
radii, so duplicate arithmetic cases are not trained repeatedly. T00 uses all
ordered operand pairs when `--t00-mode exhaustive` is selected. Upper rungs use
the existing finite deterministic complete-chain decks. This is *not* a claim
of all possible arithmetic expressions at upper depths.

For a **fast mechanism smoke only**, add `--case-cap 2` to both commands. That
caps the number of cases per rung and signed residual and sets
`full_corpus=false` in the report. Do **not** use a capped run to claim that the
entire corpus improved. Use a **different output directory** when changing the
corpus or training configuration.

## Evidence

The report provides the initial, trained-Qwen, and glass-stage mean
**teacher-forced token cross-entropy**, wrong-token counts, and a table for
every `(rung, signed delta)` cell. It separately records actual **unassisted
greedy packet accuracy** with and without the glass (default two representative
cases per cell), including fixes, new regressions, and the net correction
count. The latter is what tests whether lower token loss improves real answers.

The baseline candidate bank is built using the **wrong next-token positions**
from the frozen Qwen baseline. Candidate activations are reservoir-sampled within each rung/delta group, then
modes are selected round-robin across depth and signed residual; a small
number are kept per group. During glass training every
case is visited, but the corrective loss is supervised only on positions where
the baseline's teacher-forced next-token prediction was incorrect. The glass
also pays a squared-correction penalty to discourage unnecessary deformation.
These are *local correction training targets*, not new targets for Qwen's LoRA.

The runner validates exact no-op behavior before training and hashes the Qwen
LoRA before and after glass training, failing if those weights have changed.
Baseline/corpus/checkpoint lineage and projection settings are verified on
resume. Head weights remain frozen throughout both phases.

## Boundaries

- This first smoke tests the **Qwen final-hidden-state → LM-head interface**.
  It does not train the HybridLink detector, heads, router, or other interfaces.
  The glass type can later be reused at those interfaces if this one proves
  useful.
- Correct text generation is **not guaranteed** by lower teacher-forced loss.
  Generation is measured separately, and the experiment is not automatically
  promoted into the existing training ladder.
- A gate that is exactly zero outside its mode neighborhood can still overlap
  a correct activation **inside** that neighborhood. The report explicitly
  counts new greedy errors; a glass causing regressions is not automatically
  accepted as superior.
- No CPU test proves faster or more accurate CUDA training. Comparison with a
  fixed-budget additional Qwen training pass is required to establish the
  memory/compute advantage.
