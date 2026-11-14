# Qwen numerical-terminal glass: edge-first continuous A/B smoke (v1)

## Objective

Replace the historical mode-bank deformation *smoke entry point* with a
controlled side-by-side comparison of two **continuous** corrective networks.
Neither variant retrains Qwen. Both are tested on an immutable checkpoint
snapshot from the **currently running numerical-terminal ladder**:

`C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1\working.pt`

The objective is to correct genuine greedy `PROBLEM: ...` reduction errors
near the active frontier, including just inside and outside its current radius
and rung, without damaging the current model's successful reductions. This is
NOT the old `ANSWER:` failure inventory. No gold answer enters glass inference.

## Architecture and experimental controls

```
frozen numerical-terminal Qwen decoder ── multi-layer hidden activations H
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    │                                                   │
 A: single residual neural function                     B: same function plus soft gate
     delta = f_A(H)                                      delta = sigmoid(g_B(H))*f_B(H)
                    │                                                   │
             h'=h+delta                                           h'=h+delta
                    └─────────────────────────┬─────────────────────────┘
                                  frozen original Qwen LM head
```

Both output projections are **exactly zero-initialized**, so both initially
produce Qwen's unmodified hidden state and greedy answer. This is verified
against the real sampled inference cases before training. The gate is a **soft
learned scalar multiplier**, not a hard switch. A and B have nearly equal
parameter counts, with B's extra scalar head separately counted. There is no
mode/key bank, token-score detector, gold-answer gate, or oracle at inference.

The separate deterministic `is_numerical` recognizer is NOT the glass gate:
`PROBLEM: 0` is already terminal; `PROBLEM: 5+(-5)` still requires reduction.
HybridLink, the learned HybridLink detector, and exact-Z readout are not
trained or executed by this isolated glass experiment.

## Input checkpoint and safety

The first launch makes an immutable `source_snapshot.pt` with SHA-256 and
source metadata. Later runs reuse that snapshot while the original ladder
continues advancing independently. A legacy `ANSWER:` ladder is rejected.
The Qwen pretrained weights, LoRA, and LM head are frozen; a LoRA digest is
checked after each paired training round. All generated outputs go to a
**new** experiment directory. Old `baseline.pt`, `glass.pt`, and refinement
artifacts remain intact. The earlier mode-bank implementation is archived as
`tools/nanojev_qwen_lm_deformation_legacy.py`, and its dependent tools use that
explicit import. This replaces the primary smoke entry point only.

## Edge discovery: explore inward AND outward together

Determine the center from the snapshot `state.rung` and `state.radius_index`.
For each selected rung near the active rung (plus a T00 diagnostic), draw
signed residuals at current R, R-1, R-2, R+1, R+2, R+3, including zero and
positive/negative cases. Cover small, medium, and large operand bands; do NOT
start with an origin-first exhaustive T00 sweep. The probe is finite,
deterministic, and bounded. Each tested baseline output is independently
classified by exact one-hop greedy correctness. Baseline failures are written
with source, gold target, actual output, and inside/edge/outside grouping.

The snapshot's stage metadata proposes where to probe; **it is not a claim
that the selected radius is mastered**. The actual greedy census determines
which cases are failures and which are preservation examples.

## Paired training

For each round, sample a common error-rich plus correct-neighbor evaluation
deck (same case IDs for both variants). Every optimizer step selects exactly
the **same mathematical problem** for A and B, and the same originally-correct
replay case. Steps alternate A then B. Train the selected glass on its **first
incorrect greedy token** under its own current rollout, using the mathematically
correct target token *only in the training loss* through the frozen LM head.
When the selected case has become correct, train its residual toward zero;
replay baseline-correct cases at varied output-token positions and train
residual toward zero there as well. B also receives gate supervision from
these labeled training states. This is an observational training label,
not inference-time correctness detection.

Regenerate actual greedy outputs after each round. Reports give baseline
correct/incorrect count, A/B correct/fixed/regressed counts, number of original
correct cases receiving nontrivial deformation, mean/max residual norm,
optional B gate probability, duration, and history. Periodically perform a
full *frontier-sample* comparison. Round samples are clearly labeled as such.
Each round stores independent A and B network AND optimizer state in a shared
round directory, marked complete only once both have been saved. Restart uses
the latest complete pair. No model change is made to the live ladder.

## Full retention versus quick probes

**The edge-first smoke cannot certify the full historical mathematical corpus.**
Use `--stage audit --audit-scope certified` to re-generate the originally
certified decks on the frozen Qwen and both glasses. This is potentially long
and is intentionally separate from the fast paired acquisition rounds. The
optional `--audit-limit` is noncertifying. A claim of exact retention requires
all relevant certified cases, not just a sample, and zero regressions.

The smoke does not automatically stack glass layers or declare saturation.
An adaptive controller can later use repeated fix-A/break-B conflicts, joint
recovery attempts, and certified retention to determine whether more capacity
is justified. The smoke's `--rounds` is an execution chunk, not an arbitrary
competency or saturation threshold; invoking it again resumes the paired run.

## PowerShell setup

From `C:\Users\subsi\main_computer` with the intended Python environment:

```powershell
$py = "..\NanoJev\.venv\Scripts\python.exe"
$abArgs = @(
  '--device','cuda',
  '--stage1-checkpoint','C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt',
  '--text-checkpoint','C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt',
  '--ladder-working','C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1\working.pt',
  '--output','C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_glass_edge_ab_v1'
)

# First census, immutable model snapshot, and known-failure inventory:
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage probe @abArgs

# Paired continual training in bounded, restartable chunks:
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage train @abArgs

# Quick paired audit of the sampled edge:
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage audit --audit-scope frontier @abArgs

# Longer exact full audit of the snapshot's certified ladders:
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage audit --audit-scope certified @abArgs

# Read-only progress overview:
& $py -m tools.nanojev_qwen_lm_deformation_smoke --stage status --output 'C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_glass_edge_ab_v1'
```

For a single invocation, `--stage run` means probe followed by paired training.
Defaults: 240 edge probes, 96 matched evaluation cases per round, complete
frontier-evaluation every third round, 12 optimizer steps per branch per round,
and 6 rounds per invocation. These budgets limit the smoke's execution work;
**none are a glass-stacking or mastery threshold**. Change probe composition
only in a fresh output directory, as the frozen deck and policy are hashed.

Artifacts include `source_snapshot.pt`, `source_meta.json`, `frontier.json`,
`baseline.json`, `baseline_defects.json`, `glass_A.pt`, `glass_B.pt`,
`rounds/round_*/`, `comparison.json`, `audit_frontier.json`, and optional
`audit_certified.json`.

## Honest limitations

- Neither continuous function can guarantee zero deformation on *unseen* correct
  cases. The experiment measures real intervention and regression rates.
- Wrongness is supervised from gold **during training**; runtime sees only
  neuronal activations. A non-separable representation may prevent reliable
  correction, even with more glass layers.
- A successfully emitted exact `PROBLEM: <integer>` packet is assessed as one
  reduction step. This does not prove end-to-end HybridLink completion or Z
  extraction.
- This is CPU-unit-tested code, but real CUDA training depends on the actual
  Qwen checkpoint and is not claimed as executed in the patch build environment.
