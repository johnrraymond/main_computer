# NanoJev EMP: four-backbone outward ladder

## Purpose

Resume *outward* residual-radius acquisition from the latest independent four-bank EMP experiment, rather than restarting the older one-backbone ladder and discarding the learned left/right EMP weights. This is a **research-only branch**, not a replacement for the historic outward/return campaign or an arithmetic certification.

The origin for this run is the current `nanojev_emp_dual_bank_512_v1/latest.pt` at 3,000 steps. **Do not claim it has passed ±512 merely because earlier probes did**: its most recent `qualified_80_50` value was false. The new runner takes a byte-exact local snapshot of the four banks and reevaluates the ±512 gate on frozen DEV cards, then, once qualified, advances to ±1,024, ±2,048, and on toward ±4,000,000.

## Architecture and training

- Four *complete*, independent `AdditionNet` networks: `left.left`, `left.right`, `right.left`, `right.right`.
- All four inherit their **actual pre-trained weights** from the dual-bank latest checkpoint. The source checkpoint, its original source readouts, and source configuration are copied into the new campaign; future edits to the original run don't change the snapshots.
- All four update from the true **TRAIN answer** using the existing sign and decimal-digit cross-entropy. Each has its own learning rates, optimizer and sampling emphases. The existing right-answer comparison makes **left** banks emphasize TRAIN examples where their right counterparts are closer to the true result. The right banks learn independently against the gold answers (reciprocal right-from-left training is **not** newly introduced in this patch).
- Across radii, all four **model weights** are carried forward. The AdamW optimizer is **reinitialized at each new radius**, by design to prevent a new magnitude regime inheriting the previous stage's stale momentum. Within each radius, interrupted optimizer state is saved and restored exactly. This is a research policy choice, not a claim that carrying momenta would be worse.
- The first radius is the source radius (±512); when it passes, the runner proceeds in geometrically increasing steps. The next radius is ±1,024. Magnitude sampling mixes newly introduced frontier cases with broader arithmetic, using the existing outward curriculum.

## Advancement and retained safeguards

Advance only after **two consecutive probes** where *both* DEV A and DEV B satisfy:

| Which actual bank | Frontier | Broader outer |
| --- | ---: | ---: |
| `left.left` | >=80% within 5% | >=80% within 5% |
| `right.right` | >=50% within 5% | >=50% within 5% |

The other two banks remain independently trained and evaluated, with their histograms and paired correctness in the reports. This changes the former diagnostic `research_gate`, which checked `right.left` rather than the actual `right.right` bank; it does **not** alter the old trainer. No gates on zero or legacy during outward acquisition, though all banks report DEV distributions including zero, inner, repeated-digit and tipping violations. The full reverse consolidation and independent sealed test are **not implemented for four-bank architecture in this patch**. Do not treat outward target reached as final conformity.

The original shared-backbone EMP population and four-bank runs are never edited.

## Run

Apply ZIP files rooted at `main_computer_test/` into your `main_computer` checkout, using your usual patch harness. Start in your Python environment:

```powershell
& $py -m tools.nanojev_emp_dual_bank_ladder `
    --source-run "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --device cuda `
    --target 4000000 `
    --left-gate 0.80 `
    --right-gate 0.50 `
    --gate-streak 2 `
    --stage-steps 10000 `
    --probe-every 250 `
    --max-stages 16
```

A radius that doesn't meet its gate after 10,000 steps refuses advancement with `halted_budget_exhausted`. To extend its budget, repeat **the same command**, increase `--stage-steps` (for example to `20000`) and add `--resume`. Do not switch the `--output` to the existing four-bank run.

For a short smoke test on CUDA, use a fresh output and `--stage-steps 2 --probe-every 1 --max-stages 1` (this is a smoke, not evidence of scientific progress).

## Change training pressure while running

The **same** `tools.nanojev_emp_dual_bank_tune` works with the new campaign's `controls.json`. Example:

```powershell
& $py -m tools.nanojev_emp_dual_bank_tune `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --bank right.right `
    --repeat-boost 10 `
    --backbone-lr 0.00002
```

The current settings inherited from the source checkpoint include your earlier right-bank emphasis. Control revisions apply at the next training probe boundary and are included in each checkpoint and event log. Tuning on the new run does not affect the original.

## Reports

Each stage `stages/outward_<radius>/` contains a fixed stage-parent configuration, optional baseline, `latest.pt`, `history.json`, `histogram.svg`, `result.json`, and—**only on successful qualification**—`accepted.pt` and `acceptance.json`. `EMP_LADDER_PROBE` reports frontier/outer accuracy for actual gated banks and tipping-point violations for all four. `EMP_LADDER_STAGE_ACCEPTED` and `EMP_LADDER_SUMMARY` distinguish provisional research milestones from budget exhaustion.

Byte-level snapshot checks, source-code hashes, source/accepted-checkpoint lineage, immutable sampling/gate configurations, resume optimizer/RNG states, and independent TEST embargo protect the experiment against silently reinterpreting results. Stage budget and max-stages are intentionally *not* immutable, allowing non-destructive retries. Source snapshots remain authoritative if their original run has advanced since this branch was created.

## Verification

Tested CPU control-flow, two-radius advancement, full model state transfer, stage budget extension/resume, live tuning, drift refusal, and parent experiment regression tests. No real CUDA acceptance or million-scale capability was measured when authoring this patch.
